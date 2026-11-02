# -*- coding: utf-8 -*-
"""
crawl_competitor.py — 경쟁사·시너지세권(영어유치원) 지점 목록 월 1회 크롤링

각 브랜드 공식 홈페이지의 **공개 지점 안내 페이지**에서 지점명·주소를 수집한다.
출력 스키마는 `load_competitor.py`(xlsx 로더)와 **완전히 동일**해서
`build_competitor.py` 가 둘을 구분 없이 먹는다.

  출력  data/raw/competitor/crawl_<ym>.json              (전체 실행)
        data/raw/competitor/crawl_<브랜드>_<ym>.json      (--brand 부분 실행)
  대상  brands.py 의 `url` 이 있는 브랜드 (자사는 제외 — 내부 자료가 정본)

사용법
  python scripts/crawl_competitor.py                    # 전체
  python scripts/crawl_competitor.py --brand soma       # 한 브랜드만
  python scripts/crawl_competitor.py --ym 202608        # 기준월 지정 (기본 = 이번 달)
  python scripts/crawl_competitor.py --dry-run          # 파일 안 쓰고 건수만

사이트별 수집 경로 (2026-08-03 확인)
  soma     POST /brand/map/list.json      page_num 1..page_tot · campusList[]
  adamriz  GET  /center/                  HTML 인라인 `allCenters: [...]` JSON
  fields   GET  /sub/branchcampus.php     정적 <tr data-latitude> 테이블 (좌표 제공)
  poly     GET  /aboutpoly/campusList.do  정적 <p class="loca"> 목록
  rise     GET  /find/find__list          목록엔 주소가 없다 → 상세페이지 N회 방문
  bull     GET  /branch/?page=N           imweb 지도위젯. 10건/페이지 · 좌표 제공
  (2026-09-17 추가)
  cmath    POST /aca/center_list.asp      GotoPage=N · 20건/페이지 · EUC-KR
  cms_mt   GET  creverse.com/branch-map/branch-map-list.json  brchType=MT 만 · 좌표 제공
  cms_mg   (같은 JSON)                    brchType=MG 만
  brain    GET  /inquiry/center/list.do   JSON centerList
  hlsi     GET  /sub/sub02_01.php         정적 <dl> 8건
  u2m      GET  /Campus/List?idx=113      인라인 `var $objAcademy = [...]`

⚠️ 함정
  * **조용한 실패가 진짜 위험이다.** 사이트 개편으로 셀렉터가 깨지면 예외가 아니라
    "0건 수집"으로 끝난다. 그 결과는 지도에서 "경쟁사 없는 좋은 상권"이 된다.
    → 브랜드별 `min_expect` 미달이면 **그 브랜드를 통째로 버리고 실패로 보고**한다.
      실패한 브랜드는 출력에 안 들어가므로 `build_competitor.py` 가 직전 회차
      데이터를 계속 쓴다(= 조용히 사라지지 않는다).
  * 라이즈는 지점당 1회씩 상세페이지를 연다. `--delay` 로 간격을 두고,
    기본값을 0.5초로 잡았다. 남의 서버다.
  * 아담리즈는 `sido` 에 **`전남광주통합특별시`** 를 이미 쓴다(2026-07-01 통합 반영).
    SGIS 지오코딩은 구 표기를 알므로 `addr_norm` 이 광주/전남 후보를 함께 만든다.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brands as B                                          # noqa: E402
from addr_norm import candidates, dedupe_key, sido_of       # noqa: E402
from load_competitor import norm_name                       # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "competitor"
SNAP = ROOT / "data" / "raw" / "competitor" / "_snapshot"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def fetch(url: str, ref: str | None = None, data: dict | None = None,
          timeout: int = 30) -> str:
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    if ref:
        h["Referer"] = ref
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        h["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=h, data=body)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        raw = r.read()
    enc = "utf-8"
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore")
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def strip_tags(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
          .replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


# ==========================================================================
# 브랜드별 어댑터 — 각각 [{"name":..., "addr":..., "lat":?, "lng":?}] 를 반환
# ==========================================================================

def crawl_soma(delay: float) -> list[dict]:
    """POST /brand/map/list.json — page_num 으로 페이지네이션."""
    url = "https://new.somai.co.kr/brand/map/list.json"
    ref = "https://new.somai.co.kr/brand/map"
    out, page, tot = [], 1, 1
    while page <= tot:
        j = json.loads(fetch(url, ref, {"page_num": page}))
        d = j.get("data") or j
        tot = int(d.get("page_tot") or 1)
        for c in d.get("campusList") or []:
            nm = (c.get("HOMEPAGE_NAME") or c.get("CENTER_NAME") or "").strip()
            ad = (c.get("ADDR") or "").strip()
            if nm and ad:
                out.append({"name": nm, "addr": ad})
        page += 1
        if delay:
            time.sleep(delay)
    return out


def crawl_adamriz(delay: float) -> list[dict]:
    """HTML 인라인 `allCenters: [...]` 를 통째로 파싱."""
    h = fetch("https://www.mathplay.co.kr/center/")
    i = h.find("allCenters")
    if i < 0:
        return []
    s = h.find("[", i)
    # 괄호 균형으로 배열 끝을 찾는다 (문자열 안의 괄호는 무시)
    depth, j, instr, esc = 0, s, False, False
    while j < len(h):
        ch = h[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        else:
            if ch == '"':
                instr = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
        j += 1
    arr = json.loads(h[s:j])
    out = []
    for c in arr:
        if c.get("deleted") or str(c.get("useType", "y")).lower() != "y":
            continue
        if str(c.get("worldType") or "DOMESTIC").upper() != "DOMESTIC":
            continue                                   # 해외 캠퍼스 제외
        nm = (c.get("name") or "").strip()
        ad = " ".join(x for x in [(c.get("address") or "").strip(),
                                  (c.get("addressDetail") or "").strip()] if x)
        if nm and ad:
            out.append({"name": nm, "addr": ad})
    return out


def crawl_fields(delay: float) -> list[dict]:
    """정적 테이블. `data-latitude/longitude` 로 **좌표까지 제공**한다."""
    h = fetch("https://fieldsgood.co.kr/sub/branchcampus.php")
    out = []
    for tr in re.findall(r"<tr[^>]*data-sido=.*?</tr>", h, re.S | re.I):
        nm = re.search(r'class="branch_name"[^>]*>(.*?)</td>', tr, re.S | re.I)
        if not nm:
            continue
        # ⚠️ "시도명으로 시작하는 td" 로 고르면 안 된다 —
        #    센터명이 `대구/수성센터` 라서 이름 칸이 먼저 걸린다(실제로 13건이 그렇게 샜다).
        #    주소는 **도로명·지번 숫자가 있는 칸**으로 판정한다.
        addr = ""
        for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I):
            txt = strip_tags(t)
            if "branch_name" in t:
                continue
            if re.search(r"(?:로|길)\s*\d|\d+길\s*\d|[가-힣]동\s+\d", txt):
                addr = txt
                break
        lat = re.search(r'data-latitude="([\d.]+)"', tr)
        lng = re.search(r'data-longitude="([\d.]+)"', tr)
        name = strip_tags(nm.group(1))
        if name and addr:
            out.append({"name": name, "addr": addr,
                        "site_lat": float(lat.group(1)) if lat else None,
                        "site_lng": float(lng.group(1)) if lng else None})
    return out


def crawl_poly(delay: float) -> list[dict]:
    """정적 목록. 주소는 `<p class="loca">`, 캠퍼스명은 같은 tr 의 첫 td."""
    h = fetch("https://www.poly-english.com/aboutpoly/campusList.do")
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S | re.I):
        loca = re.search(r'class="loca"[^>]*>(.*?)</p>', tr, re.S | re.I)
        if not loca:
            continue
        # setMapData('주소', '캠퍼스명') 이 이름을 정확히 준다
        sm = re.search(r"setMapData\('(.*?)',\s*'(.*?)'\)", tr, re.S)
        if sm:
            addr, name = strip_tags(sm.group(1)), strip_tags(sm.group(2))
        else:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
            name = strip_tags(tds[0]) if tds else ""
            addr = strip_tags(loca.group(1))
        if name and addr:
            out.append({"name": name, "addr": addr})
    return out


def crawl_rise(delay: float) -> list[dict]:
    """목록엔 주소가 없다 → 캠퍼스별 상세페이지를 한 번씩 연다."""
    base = "https://www.risekorea.com"
    seen, links = set(), []

    # ⚠️ 목록은 **페이지당 6건씩 페이지네이션**된다. 1페이지만 읽으면 6건으로 끝난다
    #    (min_expect 가드가 이걸 잡아냈다). 새 항목이 안 나올 때까지 넘긴다.
    page, empty_pages = 1, 0
    while page <= 40 and empty_pages < 2:
        url = (f"{base}/find/find__list" if page == 1
               else f"{base}/find/find__list.html?page={page}")
        try:
            lst = fetch(url, ref=f"{base}/find/find__list")
        except Exception as e:
            print(f"      ! 목록 {page}페이지 실패: {type(e).__name__}")
            break
        found = 0
        for m in re.finditer(
                r'<div class="tag">(.*?)</div>.*?<p class="bel">(.*?)</p>.*?'
                r'<a href="(/[^"]+)"\s+class="link"', lst, re.S):
            tag, name, href = strip_tags(m.group(1)), strip_tags(m.group(2)), m.group(3)
            if href in seen:
                continue
            seen.add(href)
            links.append((name, tag, href))
            found += 1
        empty_pages = empty_pages + 1 if found == 0 else 0
        page += 1
        if delay:
            time.sleep(delay)
    print(f"      목록 {len(links)}건 ({page-1}페이지)")

    out = []
    for i, (name, tag, href) in enumerate(links, 1):
        try:
            d = fetch(base + href, ref=f"{base}/find/find__list")
        except Exception as e:
            print(f"      ! {name} 상세 실패: {type(e).__name__}")
            continue
        addr = ""
        for m in re.finditer(
                r"(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|"
                r"경북|경남|제주)[가-힣A-Za-z]*\s+[^<>\n']{6,80}", d):
            t = m.group(0).strip()
            if re.search(r"(로|길|동)\s*\d", t):        # 도로명·지번이 있는 줄만
                addr = t
                break
        if addr:
            out.append({"name": name, "addr": addr})
        else:
            print(f"      ! {name} 주소 미검출 ({href})")
        if delay:
            time.sleep(delay)
    return out


_GWAN_IN_NAME = re.compile(r"(\d+)\s*관")


def crawl_bull(delay: float) -> list[dict]:
    """생각하는황소 — imweb '지도 리스트' 위젯. 목록·주소·좌표가 정적 HTML 에 다 있다.

    ⚠️ **마지막 페이지를 넘어가도 끝났다는 신호가 없다.** 9페이지(3건)를 지나
       10·11·12… 를 요청하면 404 도 빈 목록도 아니고 **9페이지를 계속 돌려준다.**
       라이즈처럼 `found == 0` 으로 끊으면 40페이지까지 헛돌면서 같은 3건을
       반복해서 받는다 → **'새 지점이 안 나오면' 멈춘다.**

    ⚠️ 같은 주소에 `목동` / `목동 2관` 이 실재한다(분당·송파도 마찬가지).
       주소만으로 중복 판정하면 3개 지점이 사라지므로 지점명의 `N관` 을
       `unit` 으로 넘겨 중복 키를 가른다(§3-③ 과 같은 취지).
    """
    base = "https://www.thinking-bull.com/branch/"
    seen: set[str] = set()
    out: list[dict] = []
    page, dry = 1, 0
    while page <= 40 and dry < 2:
        h = fetch(f"{base}?sort=NAME&keyword_type=all&page={page}", ref=base)
        fresh = 0
        for blk in re.findall(
                r'<div class="map_contents inline-blocked">(.*?)<div class="btn-gruop',
                h, re.S):
            nm = re.search(r'<div class="tit">(.*?)</div>', blk, re.S)
            ad = re.search(r'<p class="adress">(.*?)</p>', blk, re.S)
            if not (nm and ad):
                continue
            name, addr = strip_tags(nm.group(1)), strip_tags(ad.group(1))
            if not name or not addr or name in seen:
                continue
            seen.add(name)
            g = _GWAN_IN_NAME.search(name)
            r = {"name": name, "addr": addr, "unit": (g.group(1) + "관") if g else ""}
            x = re.search(r'_pos_x_temp" value="([-\d.]+)"', blk)      # 경도
            y = re.search(r'_pos_y_temp" value="([-\d.]+)"', blk)      # 위도
            if x and y:
                r["site_lat"], r["site_lng"] = float(y.group(1)), float(x.group(1))
            out.append(r)
            fresh += 1
        dry = dry + 1 if fresh == 0 else 0
        page += 1
        if delay:
            time.sleep(delay)
    print(f"      목록 {len(out)}건 ({page-1}페이지)")
    return out


def crawl_cmath(delay: float) -> list[dict]:
    """시매쓰 — ASP 목록. `POST GotoPage=N`, 20건/페이지(2026-09 기준 150건·8페이지).

    주소는 `goInfoPage('코드')` 링크 글자, 지점명은 지도 버튼의 두 번째 인자다.
    ⚠️ 원문이 EUC-KR 이다 — `fetch` 가 meta charset 을 읽어 처리한다.
    """
    url = "https://cmath.co.kr/aca/center_list.asp"
    out, seen, page = [], set(), 1
    while page <= 40:
        h = fetch(url, ref=url, data={"GotoPage": page, "SrchSido": "", "SrchWord": ""})
        fresh = 0
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
            ad = re.search(r"goInfoPage\('(\d+)'\); return false;\">(.*?)</a>", tr, re.S)
            if not ad or ad.group(1) in seen:
                continue
            seen.add(ad.group(1))
            nm = re.search(r"goInfoPage\('\d+','(.*?)'\)", tr)
            out.append({"name": nm.group(1) if nm else ad.group(1),
                        "addr": strip_tags(ad.group(2))})
            fresh += 1
        if not fresh:                    # 마지막 페이지 다음은 빈 목록
            break
        page += 1
        if delay:
            time.sleep(delay)
    print(f"      목록 {len(out)}건 ({page-1}페이지)")
    return out


def _crawl_creverse(brch_type: str) -> list[dict]:
    """크레버스 통합 JSON(청담·April·CMS·iGARTEN·코딩 325건)에서 한 브랜드만 고른다.

    MT = CMS영재교육센터, MG = CMS영재관. ⚠️ 같은 상가에 영재관·영재교육센터가 함께 있다
    (노원 중계로 225) — 브랜드를 분리 등재했으니 dkey 가 브랜드별이라 둘 다 산다.
    """
    j = json.loads(fetch("https://creverse.com/branch-map/branch-map-list.json?brchType=all",
                         ref="https://creverse.com/admission/"))
    out = []
    for b in j["data"]["items"]["branches"]:
        if b.get("brchType") != brch_type:
            continue
        r = {"name": (b.get("brchName") or "").strip(), "addr": (b.get("brchAddress") or "").strip()}
        ll = (b.get("brchCoordinates") or "").split(",")
        if len(ll) == 2 and ll[0].strip() and ll[1].strip():
            r["site_lat"], r["site_lng"] = float(ll[0]), float(ll[1])
        if r["name"] and r["addr"]:
            out.append(r)
    return out


def crawl_brain(delay: float) -> list[dict]:
    """브레인스쿨 — 페이지가 `fetch('/inquiry/center/list.do')` 로 받는 JSON."""
    j = json.loads(fetch("https://www.brainschool.co.kr/inquiry/center/list.do",
                         ref="https://www.brainschool.co.kr/inquiry/center.do"))
    out = []
    for c in j.get("centerList") or []:
        if c.get("centerStatus") != "Y":
            continue
        addr = " ".join(x.strip() for x in [c.get("centerAddr") or "", c.get("centerAddrDetail") or ""] if x.strip())
        if c.get("centerName") and addr:
            out.append({"name": c["centerName"].strip(), "addr": addr})
    return out


def crawl_hlsi(delay: float) -> list[dict]:
    """한생연 — 정적 HTML 한 페이지. `<dt>지점명</dt>` + `<dd>주소 ...</dd>`.

    ⚠️ 분당센터 주소가 시도 없이 `성남시 분당구` 로 시작한다 — addr_norm 후보·지오코딩에서 확인.
    """
    h = fetch("https://hlsi.co.kr/sub/sub02_01.php")
    out = []
    for dl in re.findall(r"<dl>(.*?)</dl>", h, re.S):
        nm = re.search(r"<dt>(.*?)</dt>", dl, re.S)
        ad = re.search(r"주소</span>(.*?)</dd>", dl, re.S)
        if nm and ad:
            out.append({"name": strip_tags(nm.group(1)), "addr": strip_tags(ad.group(1))})
    return out


def crawl_u2m(delay: float) -> list[dict]:
    """유투엠 — 페이지 인라인 `var $objAcademy = [...]` JSON.

    ⚠️ 정규식으로 배열 끝(`];`)을 찾으면 **예외 없이 0건**이 된다(2026-09-17 첫 시도에서 실제로 겪음).
       `raw_decode` 로 JSON 이 끝나는 자리를 파서가 정하게 한다.
    ⚠️ 주소·전화가 모두 빈 캠퍼스가 있다(시흥장현·아산탕정 — 오픈 예정 추정) → 버린다.
    """
    h = fetch("https://www.u2math.co.kr/Campus/List?idx=113")
    i = h.find("var $objAcademy")
    if i < 0:
        return []
    arr = json.JSONDecoder().raw_decode(h, h.index("[", i))[0]
    out = []
    for a in arr:
        addr = re.sub(r"\s+", " ", f"{a.get('ADDRESS1') or ''} {a.get('ADDRESS2') or ''}").strip()
        if a.get("ACAD_NAME") and addr:
            out.append({"name": a["ACAD_NAME"].strip(), "addr": addr})
    return out


ADAPTERS = {
    "soma": crawl_soma,
    "adamriz": crawl_adamriz,
    "fields": crawl_fields,
    "poly": crawl_poly,
    "rise": crawl_rise,
    "bull": crawl_bull,
    "cmath": crawl_cmath,
    "cms_mt": lambda delay: _crawl_creverse("MT"),
    "cms_mg": lambda delay: _crawl_creverse("MG"),
    "brain": crawl_brain,
    "hlsi": crawl_hlsi,
    "u2m": crawl_u2m,
}


# ==========================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description="경쟁사 지점 목록 크롤링")
    ap.add_argument("--brand", action="append", default=[],
                    help="brands.py 의 key (soma/adamriz/fields/poly/rise)")
    ap.add_argument("--ym", help="기준월 YYYYMM (기본 = 이번 달)")
    ap.add_argument("--delay", type=float, default=0.5, help="요청 간격(초)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-guard", action="store_true",
                    help="min_expect 미달이어도 결과를 채택한다")
    a = ap.parse_args(argv)

    ym = a.ym or date.today().strftime("%Y%m")
    targets = [b for b in B.crawlable()
               if not a.brand or b["key"] in a.brand]
    if not targets:
        raise SystemExit(f"[FATAL] 대상 브랜드가 없다 (--brand {a.brand})")

    recs, ok_brands, failed = [], {}, {}
    SNAP.mkdir(parents=True, exist_ok=True)

    for bd in targets:
        print(f"[crawl] {bd['name']} … {bd['url']}")
        try:
            items = ADAPTERS[bd["key"]](a.delay)
        except Exception as e:
            failed[bd["name"]] = f"{type(e).__name__}: {e}"
            print(f"[crawl] ✗ {bd['name']} 수집 실패 — {failed[bd['name']]}")
            continue

        n = len(items)
        if n < bd["min_expect"] and not a.no_guard:
            failed[bd["name"]] = f"{n}건 < min_expect {bd['min_expect']} (셀렉터 파손 의심)"
            print(f"[crawl] ✗ {bd['name']} {n}건 — min_expect {bd['min_expect']} 미달. "
                  f"이번 회차는 **버린다**(직전 데이터 유지)")
            # 원인 규명용으로 원문을 남긴다
            try:
                (SNAP / f"{bd['key']}_{ym}_fail.html").write_text(
                    fetch(bd["url"]), encoding="utf-8")
            except Exception:
                pass
            continue

        seen_d = set()
        kept = 0
        for it in items:
            addr = it["addr"]
            cands = candidates(addr)
            if not cands:
                continue
            # 같은 건물의 별개 지점을 살리는 보조 키(예: 황소 `목동` / `목동 2관`).
            # 주소가 층·호를 안 주면 dedupe_key 가 같아져 한쪽이 사라진다.
            dk = f"{bd['key']}|{dedupe_key(addr)}" + (f"|{it['unit']}" if it.get("unit") else "")
            if dk in seen_d:
                continue
            seen_d.add(dk)
            r = {
                "id": f"{bd['key']}:{norm_name(it['name'])}",
                "dkey": dk,
                "brand_key": bd["key"],
                "brand": bd["name"],
                "brand_target": list(bd["brand_target"]),
                "category": bd["category"],
                "name": it["name"],
                "addr_raw": addr,
                "addr_cands": cands,
                "sido": sido_of(addr),
                "base_ym": ym,
                "source": "crawl",
            }
            if it.get("site_lat"):
                r["site_lat"], r["site_lng"] = it["site_lat"], it["site_lng"]
            recs.append(r)
            kept += 1
        ok_brands[bd["name"]] = kept
        print(f"[crawl] ✅ {bd['name']} {n}건 수집 → {kept}건 (중복 {n-kept})")

    # ---- id 충돌 — 이름이 달라도 정규화 후 겹칠 수 있다 ----
    # 겹친 채로 두면 build 의 이력 추적이 두 지점을 한 지점으로 보고
    # 매달 '이전'을 반복 기록한다. 로더와 같은 규칙으로 번호를 붙인다.
    from collections import Counter, defaultdict
    cnt = Counter(r["id"] for r in recs)
    seq = defaultdict(int)
    for r in recs:
        if cnt[r["id"]] > 1:
            seq[r["id"]] += 1
            print(f"[crawl] ⚠️ id 충돌 {r['brand']}/{r['name']} → {r['id']}#{seq[r['id']]}")
            r["id"] = f"{r['id']}#{seq[r['id']]}"

    print(f"\n[crawl] 합계 {len(recs):,}건 · 성공 {len(ok_brands)}개 브랜드")
    if failed:
        print("[crawl] ⚠️ 실패 브랜드 — 직전 회차 데이터가 계속 쓰인다:")
        for k, v in failed.items():
            print(f"         {k}: {v}")

    if a.dry_run:
        print("[crawl] --dry-run 이라 파일을 쓰지 않았다")
        return 1 if failed else 0
    if not recs:
        print("[crawl] 수집 0건 — 파일을 쓰지 않는다", file=sys.stderr)
        return 2

    RAW.mkdir(parents=True, exist_ok=True)
    # ⚠️ 부분 실행(--brand)이 전체 실행 결과를 덮으면 안 된다.
    #    `crawl_<ym>.json` 을 한 브랜드만 든 파일로 갈아치우면 나머지 브랜드는
    #    원천을 잃고 **지난달 xlsx 로 되돌아간다**(이력에 '이전·개명'이 역행한다).
    #    부분 실행은 파일명을 따로 쓴다 — build 는 manual_*/crawl_* 를 다 읽는다.
    part = "-".join(b["key"] for b in targets) + "_" if len(targets) < len(B.crawlable()) else ""
    out = RAW / f"crawl_{part}{ym}.json"
    out.write_text(json.dumps({
        "meta": {
            "sample": False,
            "updated": date.today().isoformat(),
            "base_ym": ym,
            "source": "브랜드 공식 홈페이지 지점 안내 크롤링",
            "urls": {b["name"]: b["url"] for b in targets},
            "counts": ok_brands,
            "failed": failed,
        },
        "data": recs,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"[crawl] 출력 {out}  ({len(recs):,}건)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
