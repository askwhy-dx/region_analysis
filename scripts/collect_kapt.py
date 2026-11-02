# -*- coding: utf-8 -*-
"""
collect_kapt.py — 공동주택(K-apt) 단지 세대수 수집  〔2026-08-03 신설, A-16〕

RTMS 실거래에는 **세대수 항목이 없다.** 그래서 아파트 세대수는 공공데이터포털의
K-apt 계열 API 두 개로만 얻을 수 있다.

  1) 단지 목록   AptListService4/getSigunguAptList4     시군구코드 → kaptCode 목록
  2) 기본 정보   AptBasisInfoServiceV5/getAphusBassInfoV5  kaptCode → 세대수·동수·주소
  ⚠️ 2026-10-12 구버전(List3·BasisV4)이 NO_OPENAPI_SERVICE_ERROR(폐기)로 바뀌어 List4·BasisV5 로 교체 — 같은 키, 필드 상위집합, kaptdaCnt 가 실수(182.0)로 온다

산출
  data/raw/kapt/list_<ym>.json     시군구별 단지 목록 (1회 받으면 재사용)
  data/raw/kapt/basis_<ym>.json    단지코드별 기본정보 — **이어받기 캐시**
  data/processed/kapt.json         행정동/시군구 집계는 build_kapt.py 가 만든다

사용법
  python scripts/collect_kapt.py                 # 이어받기 (기본 9,500건까지)
  python scripts/collect_kapt.py --max-calls 500 # 조금만
  python scripts/collect_kapt.py --refresh-list  # 단지 목록부터 다시
  python scripts/collect_kapt.py --stat          # 받은 것만 통계

⚠️ 실측으로 확인한 함정 (2026-08-03)
  * **일일 10,000건 한도**(개발계정)라 22,257단지를 하루에 못 받는다 → 3일.
    한도에 걸리면 `LIMITED_NUMBER_OF_SERVICE_REQUESTS` 가 오는데, **HTTP 200 안에
    담겨 온다.** 예외가 아니라 정상 응답처럼 보이므로 본문을 봐야 잡힌다.
  * **4스레드부터 HTTP 429** 가 쏟아진다(100건 중 19건). 3스레드(약 40건/초)가 상한이라
    그 값으로 고정했다. 8스레드로 돌렸다가 256건 중 206건을 잃었다.
  * 목록 API 는 **오퍼레이션마다 입력 코드가 다르다.** `getRoadnameAptList3` 에
    시군구코드를 넣으면 인증도 통과하고 HTTP 200 인데 **totalCount 0** 으로 조용히 빈다.
  * **세대수가 0 인 단지가 실제로 있다**(소규모·오피스텔형). 0 을 그대로 합산하면
    동별 세대수가 실제보다 **적게** 나온다 — 이 프로젝트에서 가장 위험한 방향이라
    0 건수를 항상 로그로 남기고, 집계에서는 결측으로 다룬다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "kapt"
PROC = ROOT / "data" / "processed"

LIST_API = "https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"
BASIS_API = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"

WORKERS = 2            # ⚠️ 구API 는 4부터 429, List4·BasisV5 는 3에서도 429(2026-10-12) → 2
DAILY_LIMIT = 10000    # 개발계정 트래픽 (API 별)
KEEP = ("kaptCode", "kaptName", "kaptdaCnt", "hoCnt", "kaptDongCnt", "kaptUsedate",
        "doroJuso", "kaptAddr", "bjdCode", "codeAptNm", "codeSaleNm", "zipcode")

QUOTA_MARK = "LIMITED_NUMBER_OF_SERVICE_REQUESTS"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("DATA_GO_KR_KEY") and "=" in s:
            v = s.split("=", 1)[1].strip()
            if v:
                return v
    raise SystemExit("[FATAL] .env 의 DATA_GO_KR_KEY 가 비어 있다 "
                     "— scripts/check_datago_key.py 참고")


class Quota(Exception):
    """일일 한도 소진. 여기서 멈추고 다음 날 이어받는다."""


def fetch(url: str, key: str, params: dict, tries: int = 8) -> dict:
    """serviceKey 는 이미 URL 인코딩된 값일 수 있어 직접 붙인다(이중 인코딩 방지)."""
    q = urllib.parse.urlencode(params)
    full = f"{url}?serviceKey={key}&{q}"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "jiyeok-kapt/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            if QUOTA_MARK in body:            # ⚠️ HTTP 200 안에 담겨 온다
                raise Quota()
            # ⚠️ List4·BasisV5 는 게이트웨이 오류(HTTP_ERROR 04)도 HTTP 200 본문으로 준다(2026-10-12 실측).
            #    그대로 받으면 그 시군구가 「단지 0개」로 조용히 빈다 → 재시도
            if "OpenAPI_ServiceResponse" in body[:200]:
                raise RuntimeError("gateway " + body[:160].replace("\n", " "))
            return json.loads(body)
        except Quota:
            raise
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            time.sleep((1.5 if code == 429 else 0.8) * (i + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def body_items(j: dict) -> list:
    """⚠️ **두 API 의 응답 껍데기가 다르다.**
       목록 API → `body.items[]` · 기본정보 API → `body.item`(단일 dict).
       한쪽만 보면 인증도 되고 HTTP 200 인데 **전 항목이 None 으로 채워진다**
       (실제로 40건을 그렇게 받아 세대수 0·주소 없음으로 저장했다).
    """
    b = (j.get("response") or {}).get("body") or {}
    it = b.get("items")
    if it is None:
        it = b.get("item") or []
    if isinstance(it, dict):
        it = it.get("item", it) or []
    if isinstance(it, dict):
        it = [it]
    return it if isinstance(it, list) else [it]


# --------------------------------------------------------------------------

def load_list(key: str, ym: str, refresh: bool) -> dict[str, list]:
    f = RAW / f"list_{ym}.json"
    if f.exists() and not refresh:
        d = json.loads(f.read_text(encoding="utf-8"))
        print(f"[kapt] 단지 목록 캐시 {len(d)}개 시군구 · "
              f"{sum(len(v) for v in d.values()):,}단지  ({f.name})")
        return d

    sgg = list(json.loads((PROC / "metrics_sgg.json").read_text(encoding="utf-8"))["data"])
    print(f"[kapt] 단지 목록 수집 — 시군구 {len(sgg)}개")

    def one(code):
        j = fetch(LIST_API, key, {"sigunguCode": code, "pageNo": 1,
                                  "numOfRows": 2000, "_type": "json"})
        return code, body_items(j)

    # 목록은 256건뿐이라 순차로 받는다 — List4 는 동시 호출에 게이트웨이 오류를 섞는다(2026-10-12)
    out: dict[str, list] = {}
    for code in sgg:
        out[code] = one(code)[1]
        time.sleep(0.3)
    RAW.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[kapt] 목록 {sum(len(v) for v in out.values()):,}단지 → {f.name}")
    return out


def priority(codes_by_sgg: dict[str, list]) -> list[tuple[str, dict]]:
    """자사·경쟁사가 있는 시군구를 먼저 받는다.

    3일에 나눠 받아야 하므로 **첫날부터 상담에 쓰는 지역이 채워지는 순서**가 낫다.
    (실측: 자사·경쟁사가 있는 127개 시군구가 전체 단지의 80%)
    """
    hot = set()
    f = PROC / "competitor_sgg.json"
    if f.exists():
        hot = set(json.loads(f.read_text(encoding="utf-8"))["data"])
    rows = []
    for sgg, items in codes_by_sgg.items():
        for it in items:
            rows.append((0 if sgg in hot else 1, sgg, it))
    rows.sort(key=lambda x: (x[0], x[1]))
    return [(sgg, it) for _, sgg, it in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="K-apt 단지 세대수 수집")
    ap.add_argument("--ym", default=date.today().strftime("%Y%m"))
    ap.add_argument("--max-calls", type=int, default=9500,
                    help="이번 실행에서 호출할 최대 건수 (일일 한도 10,000)")
    ap.add_argument("--refresh-list", action="store_true")
    ap.add_argument("--stat", action="store_true", help="수집 없이 현황만")
    a = ap.parse_args(argv)

    key = load_key()
    RAW.mkdir(parents=True, exist_ok=True)
    cache_f = RAW / f"basis_{a.ym}.json"
    cache: dict[str, dict] = (json.loads(cache_f.read_text(encoding="utf-8"))
                              if cache_f.exists() else {})

    lst = load_list(key, a.ym, a.refresh_list)
    targets = priority(lst)
    todo = [(sgg, it) for sgg, it in targets if it["kaptCode"] not in cache]
    print(f"[kapt] 전체 {len(targets):,}단지 · 받은 것 {len(cache):,} · 남은 것 {len(todo):,}")

    if a.stat or not todo:
        return report(cache, len(targets))

    n = min(a.max_calls, len(todo))
    print(f"[kapt] 이번 회차 {n:,}건 수집 ({WORKERS}스레드 · 일일한도 {DAILY_LIMIT:,})\n")

    hit_quota = False
    done = failed = 0
    t0 = time.time()

    def one(pair):
        sgg, it = pair
        try:
            j = fetch(BASIS_API, key, {"kaptCode": it["kaptCode"], "_type": "json"})
        except RuntimeError:       # 재시도 다 써도 실패한 단지는 비워 두고 다음 실행에 다시 받는다
            return it["kaptCode"], None
        items = body_items(j)
        rec = items[0] if items else {}
        out = {k: rec.get(k) for k in KEEP}
        out["sgg_cd"] = sgg
        # 목록에만 있는 값을 보강 (기본정보가 비어 오는 단지가 있다)
        out["kaptName"] = out.get("kaptName") or it.get("kaptName")
        out["bjdCode"] = out.get("bjdCode") or it.get("bjdCode")
        return it["kaptCode"], out

    try:
        with ThreadPoolExecutor(WORKERS) as ex:
            for code, rec in ex.map(one, todo[:n]):
                done += 1
                if rec is None:
                    failed += 1
                    continue
                cache[code] = rec
                if done % 1000 == 0:
                    el = time.time() - t0
                    print(f"       {done:,}/{n:,} · {done/el:.0f}건/초 · "
                          f"남은 시간 약 {(n-done)/max(done/el,1)/60:.1f}분")
    except Quota:
        hit_quota = True
        print(f"\n[kapt] ⛔ 일일 한도 소진 — {done:,}건까지 받았다")
    except KeyboardInterrupt:
        print(f"\n[kapt] 중단 — {done:,}건까지 받았다")

    if failed:
        print(f"[kapt] ⚠️ 재시도 끝에 실패 {failed:,}건 — 비워 뒀다, 다음 실행에서 다시 받는다")
    cache_f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n[kapt] 저장 {cache_f.name}  (누적 {len(cache):,}단지 · "
          f"{time.time()-t0:.0f}초)")

    rc = report(cache, len(targets))
    left = len(targets) - len(cache)
    if left > 0:
        print(f"\n[kapt] 남은 {left:,}단지 — "
              + ("**내일** 같은 명령을 다시 실행하면 이어받는다"
                 if hit_quota else "같은 명령을 다시 실행하면 이어받는다"))
    return rc


def report(cache: dict, total: int) -> int:
    if not cache:
        print("[kapt] 받은 것이 없다")
        return 1
    hh = [int(float(v["kaptdaCnt"])) for v in cache.values()
          if v.get("kaptdaCnt") not in (None, "")]
    zero = sum(1 for x in hh if x == 0)
    noaddr = sum(1 for v in cache.values() if not v.get("doroJuso"))
    print(f"\n[kapt] 진행률 {len(cache):,}/{total:,} ({len(cache)/total*100:.1f}%)")
    print(f"[kapt] 세대수 합계 {sum(hh):,}세대 · 평균 {sum(hh)/max(len(hh),1):,.0f}세대")
    # ⚠️ 아래 둘은 **집계를 왜곡하는 방향**이라 매 회차 눈에 보여야 한다
    print(f"[kapt] ⚠️ 세대수 0 인 단지 {zero:,}건 ({zero/max(len(hh),1)*100:.1f}%) "
          f"— 소규모·오피스텔형. 합산하면 세대수가 과소계상된다")
    print(f"[kapt] ⚠️ 도로명주소 없음 {noaddr:,}건 — RTMS 단지 매칭에서 탈락한다")
    top = Counter(v["sgg_cd"] for v in cache.values()).most_common(3)
    print(f"[kapt] 시군구 {len(set(v['sgg_cd'] for v in cache.values()))}개 · 상위 {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
