# -*- coding: utf-8 -*-
"""
collect_rtms.py — RTMS 아파트 실거래가 자동 수집 (TODO: E-1)

국토부 실거래가 공개시스템(rt.molit.go.kr)에서 **인증키 없이** 매매·전월세를
월 단위 CSV로 내려받아 파싱한다.  규격·함정은 docs/RTMS_AUTO.md 가 SSOT.

산출
  data/raw/apartment/rtms_<kind>_<YYYYMM>.csv     원본 캐시 (EUC-KR 원본 그대로)
  data/processed/apartment.json                    행정동 집계  (스키마 DATA_CONTRACT §2-4)
  data/processed/apartment_sgg.json / _sido.json   상위 집계
  data/processed/rtms_complex.json                 단지 유니크 목록 (지오코딩 입력/캐시)

사용법
  python scripts/collect_rtms.py --download-only          # 다운로드만 (기본 최근 24개월)
  python scripts/collect_rtms.py --from 202407 --to 202606
  python scripts/collect_rtms.py --no-download            # 캐시된 원본만 파싱
  python scripts/collect_rtms.py --kind sale              # 매매만
  python scripts/collect_rtms.py --aggregate-only         # 파싱→집계만 (지오코딩 캐시 사용)

주의 (docs/RTMS_AUTO.md §3)
  * 행정구역 표기는 "다운로드 시점" 기준이다. 개편 이전에 받아둔 파일과 섞지 말 것.
    → 파서가 시도 종수(16 vs 17)로 표기 세대를 자동 판별해 로그를 남긴다.
  * 같은 달을 다시 받으면 건수가 늘어난다(신고 지연). meta 에 collected_at 을 남긴다.
  * 일일 다운로드 100회 제한. 24개월 x 2종 = 48회.
  * xlsx 경로(ptXlsExcelDown.do)는 파싱이 깨진다 — CSV 경로만 쓴다.
  * form action 에 ;jsessionid= 가 박혀 있다. 하드코딩 금지 — 매번 페이지에서 추출.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("[FATAL] requests 가 필요하다:  pip install requests", file=sys.stderr)
    raise

try:                                     # Windows 콘솔(cp949)에서 ⚠ 등에 죽지 않게
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "apartment"
OUT_DIR = ROOT / "data" / "processed"

PAGE = "https://rt.molit.go.kr/pt/xls/xls.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

KINDS = {"sale": "1", "rent": "2"}      # srhDelngSecd
KIND_KR = {"sale": "매매", "rent": "전월세"}

DAILY_LIMIT = 100                        # 서버측 일일 다운로드 제한 (RTMS_AUTO §3-3)


# --------------------------------------------------------------------------
# 0. 기간 유틸
# --------------------------------------------------------------------------

def month_range(ym_from: str, ym_to: str) -> list[str]:
    y, m = int(ym_from[:4]), int(ym_from[4:6])
    ye, me = int(ym_to[:4]), int(ym_to[4:6])
    out = []
    while (y, m) <= (ye, me):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def month_bounds(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[4:6])
    if m == 12:
        nxt = date(y + 1, 1, 1)
    else:
        nxt = date(y, m + 1, 1)
    last = nxt.toordinal() - 1
    return f"{y:04d}-{m:02d}-01", date.fromordinal(last).isoformat()


def default_period(months: int = 24) -> tuple[str, str]:
    """오늘 기준 '직전 완료월'을 끝으로 하는 N개월 구간.

    진행 중인 달은 반쪽이라(RTMS_AUTO §3-2) 끝에 넣지 않는다.
    """
    t = date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    end = f"{y:04d}{m:02d}"
    ms = m - (months - 1)
    ys = y
    while ms <= 0:
        ms += 12
        ys -= 1
    return f"{ys:04d}{ms:02d}", end


# --------------------------------------------------------------------------
# 1. 다운로드
# --------------------------------------------------------------------------

def open_session() -> tuple[requests.Session, str]:
    """페이지 GET → 세션 쿠키 + CSV 다운로드 action 경로 추출.

    ⚠️ action 에 ;jsessionid=... 가 박혀 있어 재사용/하드코딩이 불가하다.
    """
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": PAGE})
    r = s.get(PAGE, timeout=30)
    r.raise_for_status()
    html = r.content.decode("euc-kr", "replace")
    m = re.search(r'action\s*=\s*"(/pt/xls/ptXlsCSVDown\.do[^"]*)"', html)
    if not m:
        raise RuntimeError("ptXlsCSVDown.do action 을 페이지에서 찾지 못했다 "
                           "(사이트 개편 가능성) — docs/RTMS_AUTO.md §2-1 확인")
    return s, "https://rt.molit.go.kr" + m.group(1)


def form_params(kind: str, ym: str) -> dict:
    frm, to = month_bounds(ym)
    return {
        "srhThingNo": "A",                 # A=아파트
        "srhDelngSecd": KINDS[kind],       # 1=매매 2=전월세
        "srhAddrGbn": "1",                 # 1=지번주소
        "srhLfstsSecd": "1",
        "srhFromDt": frm,
        "srhToDt": to,
        "srhSidoCd": "", "srhSggCd": "", "srhEmdCd": "",
        "sidoNm": "전체", "sggNm": "전체", "emdNm": "전체",
        "areaNm": "전체", "hsmpNm": "전체",
        "loadNm": "", "mobileAt": "", "srhNewRonSecd": "", "srhRoadNm": "",
        "srhLoadCd": "", "srhHsmpCd": "", "srhArea": "",
        "srhFromAmount": "", "srhToAmount": "", "srhLrArea": "",
    }


def raw_path(kind: str, ym: str) -> Path:
    return RAW_DIR / f"rtms_{kind}_{ym}.csv"


def check_body(body: bytes) -> tuple[bool, str]:
    """받은 본문이 정상 CSV 인지 검사한다.

    ⚠️ 실측 사고(2026-07-31): 202503 매매분이 **10.6MB 중 92%가 NUL 바이트**로 내려왔다.
    크기와 HTTP 200 만 보면 정상으로 보이지만, CSV 파서가 4,124행만 읽고 조용히 넘어가
    그 달이 통째로 누락된 채 평당가가 계산된다. 그래서 여기서 반드시 죽인다.
    """
    if len(body) < 100_000:
        return False, f"너무 작다({len(body):,}B)"
    nul = body.count(b"\x00")
    if nul:
        return False, f"NUL 바이트 {nul:,}개 — 다운로드 손상"
    head = body[:8000].decode("euc-kr", "replace")
    if "<html" in head.lower():
        return False, "HTML 응답(오류 페이지)"
    if "시군구" not in body.decode("euc-kr", "replace")[:20000]:
        return False, "헤더에 '시군구' 가 없다"
    return True, "ok"


def verify_cache(kinds: list[str], months: list[str]) -> list[tuple[str, str, str]]:
    """캐시된 원본 전수 검사. 손상 파일 목록을 돌려준다."""
    bad = []
    for kind in kinds:
        for ym in months:
            p = raw_path(kind, ym)
            if not p.exists():
                bad.append((kind, ym, "없음"))
                continue
            ok, why = check_body(p.read_bytes())
            if not ok:
                bad.append((kind, ym, why))
    return bad


def download(kinds: list[str], months: list[str], force: bool = False,
             sleep: float = 1.5) -> dict:
    """월 x 구분 루프. 이미 받은 달은 건너뛴다(--force 면 재수집)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    todo = [(k, ym) for k in kinds for ym in months
            if force or not raw_path(k, ym).exists()]
    if not todo:
        print("[download] 받을 것 없음 (전부 캐시됨)")
        return {"downloaded": 0, "skipped": len(kinds) * len(months)}

    if len(todo) > DAILY_LIMIT:
        raise RuntimeError(
            f"요청 {len(todo)}회 > 일일 제한 {DAILY_LIMIT}회. 기간을 나눠 받을 것 "
            f"(RTMS_AUTO §3-3)")

    print(f"[download] {len(todo)}회 요청 (일일 제한 {DAILY_LIMIT})")
    s, act = open_session()
    ok = 0
    for i, (kind, ym) in enumerate(todo, 1):
        p = raw_path(kind, ym)
        try:
            r = s.post(act, data=form_params(kind, ym), timeout=180)
            r.raise_for_status()
            body = r.content
            ok, why = check_body(body)
            if not ok:
                print(f"  [{i}/{len(todo)}] {kind} {ym}  ✗ {why} — 세션 재획득 후 재시도")
                s, act = open_session()
                time.sleep(3)
                r = s.post(act, data=form_params(kind, ym), timeout=180)
                body = r.content
                ok, why = check_body(body)
                if not ok:
                    print(f"      → 재시도도 실패({why}). 저장하지 않고 건너뜀")
                    continue
            p.write_bytes(body)
            ok += 1
            print(f"  [{i}/{len(todo)}] {kind} {ym}  {len(body):,}B → {p.name}")
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {kind} {ym}  ✗ {type(e).__name__}: {e}")
        time.sleep(sleep)
    return {"downloaded": ok, "requested": len(todo)}


# --------------------------------------------------------------------------
# 2. 파싱
# --------------------------------------------------------------------------

def find_header(rows: list[list[str]]) -> int:
    """컬럼 헤더 행 위치 탐색. 행 번호 하드코딩 금지(RTMS_AUTO §2-4)."""
    for i, r in enumerate(rows[:40]):
        cells = [c.strip() for c in r]
        if "시군구" in cells and ("NO" in cells or "번지" in cells):
            return i
    raise RuntimeError("헤더행(NO/시군구)을 찾지 못했다 — 파일 레이아웃 변경 가능성")


# 일부 월분에 따옴표가 닫히지 않은 행이 있어 기본 필드 한도(128KB)를 넘는다.
# 한도를 올려서 파일 전체가 죽는 것을 막는다 (해당 행은 아래에서 컬럼 수로 걸러진다).
csv.field_size_limit(2 ** 31 - 1)


def read_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    text = raw.decode("euc-kr", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    h = find_header(rows)
    cols = [c.strip() for c in rows[h]]
    out = []
    for r in rows[h + 1:]:
        if not r or not any(c.strip() for c in r):
            continue
        out.append({cols[i]: (r[i].strip() if i < len(r) else "")
                    for i in range(len(cols))})
    return out


def _f(v: str):
    v = (v or "").replace(",", "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def split_sigungu(s: str) -> tuple[str, str]:
    """'대구광역시 달서구 대곡동' → (시도명, 나머지). 법정동까지 한 문자열이다."""
    parts = (s or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


# --------------------------------------------------------------------------
# 3. 표기 세대 판별 (RTMS_AUTO §3-1)
# --------------------------------------------------------------------------

SIDO_NEW = "전남광주통합특별시"
SIDO_OLD = {"광주광역시", "전라남도"}


def detect_era(sido_names: set[str]) -> str:
    """시도 종수/명칭으로 구·신 표기 세대를 판별한다.

    개편 전 파일(17종·광주/전남)과 개편 후 파일(16종·전남광주통합)을 섞으면
    호남권이 통째로 조인에 실패한다 — 그래서 조용히 넘기지 않고 판별해 로그를 남긴다.
    """
    n = len(sido_names)
    has_old = bool(sido_names & SIDO_OLD)
    has_new = SIDO_NEW in sido_names
    if has_new and not has_old:
        return f"신(16종, {n}종 관측)"
    if has_old and not has_new:
        return f"구(17종, {n}종 관측) ⚠️개편 이전 표기"
    if has_old and has_new:
        return f"혼재 ⚠️{n}종 — 개편 전후 파일이 섞였다"
    return f"불명({n}종)"


# --------------------------------------------------------------------------
# 4. 단지 단위 집계 → 행정동 배정
# --------------------------------------------------------------------------

PY_PER_M2 = 0.3025          # 1㎡ = 0.3025평
EXCLUSIVE_RATE = 0.75       # 전용률 가정 → 공급면적 = 전용 / 0.75  (SCORE.md §3)

# 🆕 2026-08-04 — 이 건수 미만이면 그 동의 가격은 통계가 아니라 우연이다.
# 실측: 전국 2,689개 아파트 동 중 178개가 24개월 매매 10건 미만이었다.
MIN_DEALS = 10


def complex_key(r: dict) -> tuple:
    """단지 식별 키. 명칭만으로는 동명이 단지가 섞이므로 주소를 함께 쓴다."""
    return ((r.get("시군구") or "").strip(),
            (r.get("단지명") or "").strip(),
            (r.get("도로명") or "").strip())


def accumulate(months: list[str], kinds: list[str], verbose=True):
    """월별 CSV 를 훑어 단지 단위로 누적한다 (전체 행을 메모리에 올리지 않는다)."""
    comp: dict[tuple, dict] = {}
    rent: dict[tuple, dict] = {}
    counts: dict[str, int] = {}
    sido_all: set[str] = set()
    road_missing = 0

    for kind in kinds:
        for ym in months:
            p = raw_path(kind, ym)
            if not p.exists():
                continue
            rows = read_csv(p)
            counts[f"{kind}_{ym}"] = len(rows)
            for r in rows:
                sd, _ = split_sigungu(r.get("시군구", ""))
                if sd:
                    sido_all.add(sd)
                if not (r.get("도로명") or "").strip():
                    road_missing += 1
                k = complex_key(r)
                if kind == "sale":
                    amt = _f(r.get("거래금액(만원)"))
                    area = _f(r.get("전용면적(㎡)"))
                    if amt is None or not area:
                        continue
                    d = comp.setdefault(k, {"n": 0, "amt": [], "py": [], "built": [],
                                            "jibun": (r.get("번지") or "").strip()})
                    d["n"] += 1
                    d["amt"].append(amt)
                    d["py"].append(amt / (area / EXCLUSIVE_RATE * PY_PER_M2))
                    by = _f(r.get("건축년도"))
                    if by:
                        d["built"].append(by)
                else:
                    dep = _f(r.get("보증금(만원)"))
                    mon = _f(r.get("월세금(만원)"))
                    if dep is None:
                        continue
                    d = rent.setdefault(k, {"n": 0, "dep": [], "jeonse": []})
                    d["n"] += 1
                    d["dep"].append(dep)
                    if not mon:                      # 월세 0 = 순수 전세
                        d["jeonse"].append(dep)
            if verbose:
                print(f"  [agg] {p.name}  {len(rows):,}행 누적")
    return comp, rent, counts, sido_all, road_missing


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def build_apartment(comp, rent, geo, counts, era, road_missing, period):
    """단지 → 행정동 집계 → apartment{,_sgg,_sido}.json"""
    from collections import defaultdict

    cur_y = date.today().year
    lv = {"adm": defaultdict(lambda: {"n": 0, "amt": [], "py": [], "new": 0,
                                      "built_n": 0, "dep": [], "jeonse": []}),
          "sgg": defaultdict(lambda: {"n": 0, "amt": [], "py": [], "new": 0,
                                      "built_n": 0, "dep": [], "jeonse": []}),
          "sido": defaultdict(lambda: {"n": 0, "amt": [], "py": [], "new": 0,
                                       "built_n": 0, "dep": [], "jeonse": []})}
    placed = failed = 0
    for k, d in comp.items():
        adm = geo.get(k)
        if not adm:
            failed += 1
            continue
        placed += 1
        for level, code in (("adm", adm), ("sgg", adm[:5]), ("sido", adm[:2])):
            t = lv[level][code]
            t["n"] += d["n"]
            t["amt"] += d["amt"]
            t["py"] += d["py"]
            if d["built"]:
                t["built_n"] += len(d["built"])
                t["new"] += sum(1 for b in d["built"] if cur_y - b <= 10)
        r = rent.get(k)
        if r:
            for level, code in (("adm", adm), ("sgg", adm[:5]), ("sido", adm[:2])):
                lv[level][code]["dep"] += r["dep"]
                lv[level][code]["jeonse"] += r["jeonse"]

    out = {}
    for level, tab in lv.items():
        data = {}
        for code, t in sorted(tab.items()):
            if not t["amt"]:
                continue
            avg_py = sum(t["py"]) / len(t["py"])
            avg_amt = sum(t["amt"]) / len(t["amt"])
            n_deal = len(t["amt"])
            rec = {
                "deal_count": t["n"],
                "avg_price": round(avg_amt),
                # 소비력 ① 자산수준의 **정본은 중앙값**이다 (2026-08-04).
                # 평균은 초고가 1건에 끌려간다 — 거래가 10건뿐인 동에서 60억 1건이 섞이면
                # 그 동이 강남보다 비싸 보인다. 평균은 비교용으로만 남긴다.
                "median_price_per_py": round(_median(t["py"]) or 0),
                "avg_price_per_py": round(avg_py),
                "median_price": round(_median(t["amt"]) or 0),
                # 🆕 표본 부족 표시 — 이 동의 가격은 '싸다/비싸다'가 아니라 '모른다'다.
                # 화면은 숫자 대신 "표본 부족"을 띄운다. 0 이나 평균으로 메우면
                # 거래가 거의 없는 동네가 조용히 순위에 섞인다.
                "low_sample": n_deal < MIN_DEALS,
                "deal_n_price": n_deal,
                "households": None,        # RTMS 에는 세대수가 없다 → K-APT 보강(C-5)
                "new_ratio": (round(t["new"] / t["built_n"], 3)
                              if t["built_n"] else None),
            }
            # 🔻 전세가율 = **참고값**. 소비력 축에서 제외한다 (2026-08-04, E-2 종결).
            #    실측: 전세가율 vs 평당가 상관 **-0.545** (2,518개 동). 평당가 상위 5개 동이
            #    0.172~0.363 으로 전부 하위권이다. 전세가율은 소비력이 아니라 매매가에 낀
            #    기대 프리미엄의 역수라서, 소비력 대용으로 쓰면 **부유한 동네가 낮게** 나온다.
            #    1.0 초과 역전값도 170개 동(최대 2.665) 남아 있다 — 분자·분모가 서로 다른
            #    물건(전세는 소형·구축 / 매매는 대형·신축)이라 생기는 것이고,
            #    단지·면적·기준월을 맞춰 고쳐도 위 역상관은 그대로다. 그래서 정밀화하지 않는다.
            if t["jeonse"]:
                rec["avg_jeonse"] = round(sum(t["jeonse"]) / len(t["jeonse"]))
                rec["jeonse_ratio"] = round(rec["avg_jeonse"] / avg_amt, 3) if avg_amt else None
            data[code] = rec
        out[level] = data

    meta_common = {
        "sample": False,
        "updated": date.today().isoformat(),
        "collected_at": date.today().isoformat(),
        "source": "국토부 실거래가 공개시스템(RTMS) CSV",
        "period": f"{period[0]}~{period[1]}",
        "era": era,
        "monthly_counts": counts,
        "road_missing": road_missing,
        "complex_total": len(comp),
        "complex_placed": placed,
        "complex_geocode_failed": failed,
        "note": ("평당가는 공급면적 기준(전용률 0.75 가정, SCORE.md §3). "
                 "소비력 자산수준의 정본은 median_price_per_py(중앙값)이며 "
                 "avg_price_per_py 는 비교용이다(2026-08-04). "
                 f"low_sample=true 는 매매 표본 {MIN_DEALS}건 미만 = 가격을 '모른다'는 뜻. "
                 "new_ratio 는 준공 10년 이내 '거래' 비중 — 세대 기준 정의(계약 §2-4)와 "
                 "다르며 C-5(K-APT 조인)에서 교정한다. "
                 "jeonse_ratio/avg_jeonse 는 참고값이며 소비력 축에서 제외(E-2 종결, "
                 "평당가와 상관 -0.545)."),
        "consumption_axis": {
            "asset_level": "median_price_per_py",
            "scale": "apt_households(K-APT, C-5 이후) / households(주민등록)",
            "total": "apt_households × avg_price",
            "excluded": ["jeonse_ratio", "avg_jeonse"],
            "excluded_reason": "평당가와 -0.545 역상관 — 소비력 대용으로 쓰면 방향이 뒤집힌다",
        },
    }
    for level, fn in (("adm", "apartment.json"), ("sgg", "apartment_sgg.json"),
                      ("sido", "apartment_sido.json")):
        meta = dict(meta_common, level=level, count=len(out[level]))
        (OUT_DIR / fn).write_text(
            json.dumps({"meta": meta, "data": out[level]}, ensure_ascii=False),
            encoding="utf-8")
        print(f"  wrote data/processed/{fn}  ({len(out[level]):,}건)")
    return placed, failed


# --------------------------------------------------------------------------
# 5. main
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="RTMS 아파트 실거래 자동 수집 (E-1)")
    ap.add_argument("--from", dest="ym_from", default=None, help="YYYYMM")
    ap.add_argument("--to", dest="ym_to", default=None, help="YYYYMM")
    ap.add_argument("--months", type=int, default=24, help="기간 미지정 시 최근 N개월 (기본 24)")
    ap.add_argument("--kind", default="sale,rent", help="sale,rent")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--no-download", action="store_true", help="캐시된 원본만 파싱")
    ap.add_argument("--force", action="store_true", help="캐시가 있어도 재다운로드")
    ap.add_argument("--parse-only", action="store_true", help="파싱 요약까지만 (지오코딩 생략)")
    ap.add_argument("--verify-only", action="store_true", help="캐시 무결성 검사만")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sleep", type=float, default=1.5)
    a = ap.parse_args(argv)

    kinds = [k.strip() for k in a.kind.split(",") if k.strip() in KINDS]
    if a.ym_from and a.ym_to:
        ym_from, ym_to = a.ym_from, a.ym_to
    else:
        ym_from, ym_to = default_period(a.months)
    months = month_range(ym_from, ym_to)

    print(f"[RTMS] 기간 {ym_from}~{ym_to} ({len(months)}개월) · 구분 {kinds}")

    # 캐시 무결성 검사 — 손상분은 자동으로 재수집 대상에 넣는다
    bad = verify_cache(kinds, months)
    if bad:
        print(f"[verify] 손상/누락 {len(bad)}건: "
              + ", ".join(f"{k}_{y}({w})" for k, y, w in bad[:6])
              + ("..." if len(bad) > 6 else ""))
        if not a.no_download:
            for k, y, _w in bad:
                raw_path(k, y).unlink(missing_ok=True)
    if a.verify_only:
        return 1 if bad else 0

    if not a.no_download:
        st = download(kinds, months, force=a.force, sleep=a.sleep)
        print(f"[download] 완료 {st}")
        if a.download_only:
            return 0

    # ---- 파싱 → 단지 누적 ----
    comp, rent, counts, sido_all, road_missing = accumulate(months, kinds)
    if not comp:
        print("[parse] 매매 데이터가 없다 — 다운로드부터 확인할 것")
        return 1

    era = detect_era(sido_all)
    total = sum(counts.values())
    print(f"\n[표기세대] {era}")
    print(f"[합계] {total:,}행 / 파일 {len(counts)}개 · 도로명결측 {road_missing:,}건 "
          f"({road_missing/max(total,1)*100:.2f}%)")
    print(f"[단지] 매매 유니크 {len(comp):,} · 전월세 유니크 {len(rent):,}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "_parse_summary.json").write_text(
        json.dumps({"era": era, "collected_at": date.today().isoformat(),
                    "period": [ym_from, ym_to], "counts": counts,
                    "road_missing": road_missing, "complexes": len(comp)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    if "혼재" in era or "개편 이전" in era:
        print("⚠️ 표기 세대가 통일되지 않았다. --force 로 전량 재수집할 것 (RTMS_AUTO §3-1)")

    if a.parse_only:
        return 0

    # ---- 단지 → 행정동 (C-6 지오코딩) ----
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from geocode_addr import Geocoder

    keys = list(comp)
    # 전체 도로명주소 = 시군구(시도+시군구+법정동) + 도로명. 실패 시 지번 폴백.
    roads, jibuns = [], []
    for sgg_str, _nm, road in keys:
        head = " ".join(sgg_str.split()[:2])          # 시도 + 시군구
        roads.append(f"{head} {road}".strip() if road else "")
        jibuns.append(f"{sgg_str} {comp[(sgg_str, _nm, road)]['jibun']}".strip())
    # 도로명이 없는 단지는 지번을 1차 주소로 쓴다
    addrs = [r if r else j for r, j in zip(roads, jibuns)]

    g = Geocoder(workers=a.workers)
    res = g.batch(addrs, jibuns)
    g.save()
    g.report()

    geo = {}
    for k, ad in zip(keys, addrs):
        got = res.get(ad)
        if got:
            geo[k] = got["adm_cd"]

    # 단지 목록 저장 — POI 맵(build_poi.py)과 증분 수집이 이걸 읽는다
    comps = []
    for k, ad in zip(keys, addrs):
        got = res.get(ad)
        if not got:
            continue
        d = comp[k]
        comps.append({
            "name": k[1], "sgg_str": k[0], "road": k[2],
            "lng": got["lng"], "lat": got["lat"], "adm_cd": got["adm_cd"],
            "deal_count": d["n"],
            "avg_price": round(sum(d["amt"]) / len(d["amt"])) if d["amt"] else None,
            "avg_price_per_py": round(sum(d["py"]) / len(d["py"])) if d["py"] else None,
            "built": int(max(d["built"])) if d["built"] else None,
        })
    (OUT_DIR / "rtms_complex.json").write_text(
        json.dumps({"meta": {"sample": False, "updated": date.today().isoformat(),
                             "period": f"{ym_from}~{ym_to}", "count": len(comps)},
                    "data": comps}, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote data/processed/rtms_complex.json  ({len(comps):,}단지)")

    placed, failed = build_apartment(comp, rent, geo, counts, era,
                                     road_missing, (ym_from, ym_to))
    print(f"\n[apartment] 단지 배치 {placed:,} / 실패 {failed:,} "
          f"({failed/max(len(comp),1)*100:.2f}%)")
    if failed:
        print("⚠️ 배치 실패 단지는 그 동의 평당가 표본에서 빠진다 — 건수를 함께 보고할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
