# -*- coding: utf-8 -*-
"""
collect_neis.py — 학원·교습소 전국 수집 (TODO: B-3 → B-4)

NEIS 교육정보 개방포털 `acaInsTiInfo` 를 17개 시도교육청 루프로 전수 수집하고,
도로명주소를 geocode_addr.py(C-6) 로 행정동에 배정한다.

산출
  data/raw/academy/neis_<교육청코드>.json        원본 캐시
  data/processed/academy.json                    행정동 집계 + 시군구/시도 집계

사용법
  python scripts/collect_neis.py                 # 전국 수집 → 집계
  python scripts/collect_neis.py --no-download   # 캐시된 원본만 집계 (분야 기준만 바꿀 때)
  python scripts/collect_neis.py --office B10    # 특정 교육청만
  python scripts/collect_neis.py --all-realms    # 분야 필터 해제 (비교용, 기본 아님)

⚠️ 주의
  * 인증키가 없으면 pSize 를 무시하고 **5건만** 돌려준다. 반드시 NEIS_KEY 를 쓴다.
  * `REG_STTUS_NM` 이 '개원' 인 것만 센다. 휴원·폐원을 포함하면 경쟁강도가 과대평가된다.
  * **'학원 수' = `REALM_SC_NM` 이 `입시.검정 및 보습` 인 것만이다** (2026-08-03 관리자 지정).
    예능·어학원·독서실·직업기술은 업태가 달라 뺀다 — 상세는 `REALM_KEEP` 주석.
    원본 캐시는 전 분야를 그대로 갖고 있으므로 기준 변경은 `--no-download` 재집계로 끝난다.
  * 지오코딩은 **FA_RDNMA(도로명주소) 유니크 단위**로 돈다. 같은 건물의 학원 수십 개가
    한 번의 호출을 공유하므로 실제 호출량은 학원 수보다 훨씬 적다.
  * **지오코딩 실패 건수를 반드시 보고한다.** 조용히 버리면 comp_academy 가
    "경쟁 없는 좋은 동"으로 잘못 계산된다 (docs/ADDR_TO_ADM.md §4-4).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("[FATAL] requests 필요:  pip install requests", file=sys.stderr)
    raise

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocode_addr import Geocoder                   # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "academy"
OUT = ROOT / "data" / "processed" / "academy.json"
API = "https://open.neis.go.kr/hub/acaInsTiInfo"

OFFICES = {
    "B10": "서울특별시교육청", "C10": "부산광역시교육청", "D10": "대구광역시교육청",
    "E10": "인천광역시교육청", "F10": "광주광역시교육청", "G10": "대전광역시교육청",
    "H10": "울산광역시교육청", "I10": "세종특별자치시교육청", "J10": "경기도교육청",
    "K10": "강원특별자치도교육청", "M10": "충청북도교육청", "N10": "충청남도교육청",
    "P10": "전라북도교육청", "Q10": "전라남도교육청", "R10": "경상북도교육청",
    "S10": "경상남도교육청", "T10": "제주특별자치도교육청",
}

PAGE = 1000     # 키가 있어야 실제로 적용된다

# ---------------------------------------------------------------------------
# 집계 대상 분야 (2026-08-03 관리자 지정) — **이 상수가 '학원 수'의 정의다.**
#
# NEIS `REALM_SC_NM` 전국 실측 (개원 138,463건):
#     입시.검정 및 보습  77,711   ← 우리 경쟁 상대
#     예능(대)           35,644      피아노·미술·태권도 …
#     국제화              8,742      어학원
#     종합(대)            4,024
#     직업기술            3,980
#     기타(대)·기예(대)·독서실·인문사회·정보·특수교육  나머지
#
# 예능·독서실·직업기술까지 세면 "학원 1개당 학령인구"가 실제 경쟁보다 낮게 나와
# **경쟁이 심한 것처럼** 보인다. 가맹상담에서 같은 업태만 비교해야 말이 된다.
#
# ⚠️ 이 집합을 바꾸면 `academy_cnt` · `pupils_per_academy` · POI 학원 레이어가
#    전부 달라진다. **옛 산출물과 증감을 비교하지 말 것** (기준이 다르다).
# ⚠️ `build_poi.py` 도 이 상수를 import 한다 — 지도와 숫자가 갈라지면 안 된다.
# ---------------------------------------------------------------------------
REALM_KEEP = {"입시.검정 및 보습"}


def realm_of(rec: dict) -> str:
    return (rec.get("REALM_SC_NM") or "기타").strip()


def is_target_realm(rec: dict) -> bool:
    """입시·보습 학원인가 (관리자 정의)."""
    return realm_of(rec) in REALM_KEEP


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fetch_office(key: str, code: str, sess: requests.Session) -> list[dict]:
    rows, page = [], 1
    while True:
        r = sess.get(API, params={"KEY": key, "Type": "json", "pIndex": page,
                                  "pSize": PAGE, "ATPT_OFCDC_SC_CODE": code},
                     timeout=60)
        try:
            j = r.json()
        except Exception:
            print(f"    ! {code} p{page} JSON 파싱 실패")
            break
        if "acaInsTiInfo" not in j:
            res = (j.get("RESULT") or {})
            if res.get("CODE") not in ("INFO-200",):     # INFO-200 = 데이터 없음
                print(f"    ! {code} p{page} {res.get('CODE')} {res.get('MESSAGE')}")
            break
        body = j["acaInsTiInfo"]
        got = body[1].get("row", []) if len(body) > 1 else []
        rows += got
        total = body[0]["head"][0]["list_total_count"]
        if len(rows) >= total or not got:
            break
        page += 1
        time.sleep(0.15)
    return rows


def download(key: str, offices: list[str], force: bool = False) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    out = {}
    for i, code in enumerate(offices, 1):
        p = RAW_DIR / f"neis_{code}.json"
        if p.exists() and not force:
            rows = json.loads(p.read_text(encoding="utf-8"))
            print(f"  [{i}/{len(offices)}] {code} {OFFICES[code]}  캐시 {len(rows):,}건")
        else:
            rows = fetch_office(key, code, sess)
            p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            print(f"  [{i}/{len(offices)}] {code} {OFFICES[code]}  {len(rows):,}건")
        out[code] = rows
    return out


def num(v):
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NEIS 학원 전국 수집 (B-3)")
    ap.add_argument("--office", default=None, help="교육청 코드 (쉼표구분). 기본 전체 17개")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--all-realms", action="store_true",
                    help="분야 필터를 끄고 전 학원을 센다 (기본은 입시·보습만)")
    a = ap.parse_args(argv)

    env = load_env()
    key = env.get("NEIS_KEY", "")
    offices = [c.strip() for c in a.office.split(",")] if a.office else list(OFFICES)

    if not key and not a.no_download:
        print("[FATAL] NEIS_KEY 가 없다. 키 없이는 pSize 가 무시되고 5건만 온다.",
              file=sys.stderr)
        return 2

    print(f"[neis] 교육청 {len(offices)}개")
    if a.no_download:
        data = {}
        for c in offices:
            p = RAW_DIR / f"neis_{c}.json"
            if p.exists():
                data[c] = json.loads(p.read_text(encoding="utf-8"))
        print(f"[neis] 캐시에서 {sum(len(v) for v in data.values()):,}건")
    else:
        data = download(key, offices, force=a.force)

    total = sum(len(v) for v in data.values())
    print(f"[neis] 원본 총 {total:,}건")

    # ---- 개원만 남긴다 ----
    alive, status = [], defaultdict(int)
    for rows in data.values():
        for r in rows:
            st = (r.get("REG_STTUS_NM") or "").strip()
            status[st] += 1
            if st == "개원":
                alive.append(r)
    print(f"[neis] 등록상태 {dict(status)}")
    print(f"[neis] 개원 {len(alive):,}건 ({len(alive)/max(total,1)*100:.1f}%)")

    # ---- 분야 필터 (관리자 정의 = 입시·보습만) ----
    realm_all = defaultdict(int)
    for r in alive:
        realm_all[realm_of(r)] += 1
    if not a.all_realms:
        before = len(alive)
        alive = [r for r in alive if is_target_realm(r)]
        print(f"[neis] 분야 필터 {sorted(REALM_KEEP)} → {len(alive):,}건 "
              f"(제외 {before - len(alive):,}건)")
        for k, v in sorted(realm_all.items(), key=lambda x: -x[1]):
            if k not in REALM_KEEP:
                print(f"         − {k:18s} {v:7,}")
    else:
        print("[neis] ⚠️ --all-realms — 전 분야를 센다. "
              "이 산출물은 '입시·보습' 기준과 비교하지 말 것")

    # ---- 주소 유니크 → 지오코딩 ----
    no_addr = 0
    addrs = set()
    for r in alive:
        ad = (r.get("FA_RDNMA") or "").strip()
        if ad:
            addrs.add(ad)
        else:
            no_addr += 1
    print(f"[neis] 주소 유니크 {len(addrs):,}건 (주소없음 {no_addr:,}건)")

    g = Geocoder(workers=a.workers)
    res = g.batch(sorted(addrs))
    g.save()
    g.report()

    # ---- 행정동 집계 ----
    agg = defaultdict(lambda: {"academy_cnt": 0, "capacity": 0,
                               "by_realm": defaultdict(int)})
    placed, failed = 0, 0
    for r in alive:
        ad = (r.get("FA_RDNMA") or "").strip()
        got = res.get(ad) if ad else None
        if not got:
            failed += 1
            continue
        d = agg[got["adm_cd"]]
        d["academy_cnt"] += 1
        d["capacity"] += num(r.get("TOFOR_SMTOT"))
        d["by_realm"][(r.get("REALM_SC_NM") or "기타").strip()] += 1
        placed += 1

    print(f"\n[neis] 배치 {placed:,}건 · 지오코딩 실패로 탈락 {failed:,}건 "
          f"({failed/max(len(alive),1)*100:.2f}%)")
    print(f"[neis] 행정동 커버리지 {len(agg):,}동")

    obj = {
        "meta": {
            "sample": False,
            "updated": date.today().isoformat(),
            "collected_at": date.today().isoformat(),
            "source": "NEIS 교육정보 개방포털 acaInsTiInfo",
            "offices": len(offices),
            "rows_raw": total,
            "rows_open": len(alive),
            "status_counts": dict(status),
            "addr_unique": len(addrs),
            "addr_missing": no_addr,
            "placed": placed,
            "geocode_failed": failed,
            "adm_coverage": len(agg),
            "geocode_stat": g.stat,
            # ⚠️ 이 두 필드가 "무엇을 학원으로 셌는가"의 증거다. 옛 산출물과
            #    비교하기 전에 반드시 확인할 것 (기준이 다르면 증감이 무의미하다).
            "realm_filter": (None if a.all_realms else sorted(REALM_KEEP)),
            "realm_counts_all": dict(sorted(realm_all.items(), key=lambda x: -x[1])),
            "rows_target": len(alive),
            "note": ("REG_STTUS_NM='개원' + REALM_SC_NM 분야 필터 후 집계. "
                     "행정동은 FA_RDNMA 지오코딩 후 좌표 PIP."),
        },
        "data": {k: {"academy_cnt": v["academy_cnt"], "capacity": v["capacity"],
                     "by_realm": dict(sorted(v["by_realm"].items(),
                                             key=lambda x: -x[1])[:8])}
                 for k, v in sorted(agg.items())},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[neis] → {OUT}")
    if failed:
        print("⚠️ 탈락분은 그 동의 학원 수를 과소계상한다 → comp_academy 가 "
              "'경쟁 없는 동'으로 과대평가될 수 있으니 위 건수를 함께 보고할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
