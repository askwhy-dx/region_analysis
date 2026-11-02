# -*- coding: utf-8 -*-
"""
build_poi.py — 시군구별 POI 파일 조립 (DATA_CONTRACT §2-5)

전국 실데이터 3종을 읽어 `data/processed/poi_<sgg_cd>.json` 을 만든다.
대시보드가 동 클릭 시 지연 로딩하는 파일이다.

  입력
    data/processed/school.json          C-1  학교(초/중/고) — 좌표 원천 포함
    data/raw/academy/neis_*.json        B-3  학원 원본
    data/processed/geocode_cache.json   C-6  주소 → 좌표·행정동
    data/processed/rtms_complex.json    E-1  아파트 단지(좌표·평당가·건축년도)

  출력
    data/processed/poi_<sgg_cd>.json    {meta, school[], academy[], apartment[]}

⚠️ 이 스크립트가 기존 `collect_poi.py --sample` 산출물(더미 4개 시군구)을 대체한다.
   `meta.mode:"sample"` 이 아니라 **`meta.sample:false`** 로 쓴다 (DATA_CONTRACT §2-0, A-7 ④).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
NEIS_RAW = ROOT / "data" / "raw" / "academy"


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---------------------------------------------------------------- C-5 단지 매칭
# RTMS 단지(지도 마커)에는 세대수가 없고, K-apt 단지에는 좌표가 없다.
# 둘을 잇는 유일한 공통 키가 **도로명주소**다 (단지명은 표기가 제각각이라 못 쓴다 —
# "엘지,쌍용아파트" vs "LG쌍용").
#
# ⚠️ 매칭률은 약 51% 가 상한이다. K-apt 는 **의무관리대상 공동주택**만 담고
#    RTMS 는 소규모 아파트 거래까지 담아서, 애초에 없는 단지가 절반쯤 된다.
#    못 붙은 단지는 **null 로 둔다** — 0 으로 채우면 "세대수 0인 단지"가 되어
#    지도 팝업에서 실측 0 과 구분이 사라진다.
# ⚠️ 같은 도로명주소에 단지가 둘 이상인 키(실측 263건)는 **버린다.** 어느 쪽인지
#    모르는 채로 붙이면 팝업에 남의 단지 세대수가 뜬다.
import re                                                          # noqa: E402


def _road_key(addr: str) -> str:
    """도로명주소에서 시도·시군구를 떼고 '도로명+건물번호'만 남긴다."""
    t = str(addr or "").split()
    for i, x in enumerate(t):
        if i > 0 and re.search(r"(로|길)$|(로|길)\d", x):
            return "".join(t[i:])
    return ""


def kapt_by_road() -> dict[str, dict]:
    raw = ROOT / "data" / "raw" / "kapt"
    files = sorted(raw.glob("basis_*.json"))
    if not files:
        print("[poi] ! K-apt 수집분이 없다 — 단지 세대수는 null 로 둔다")
        return {}
    basis = read(files[-1]) or {}
    idx: dict[str, dict | None] = {}
    for r in basis.values():
        rk, sgg = _road_key(r.get("doroJuso")), (r.get("sgg_cd") or "")
        if not rk or not sgg:
            continue
        key = f"{sgg}|{rk}"
        if key in idx:
            idx[key] = None                     # 같은 주소에 둘 이상 → 포기
            continue
        hh = r.get("kaptdaCnt") or r.get("hoCnt")     # 세대수 0 이면 호수 폴백 (build_kapt 와 동일)
        y = str(r.get("kaptUsedate") or "")[:4]
        idx[key] = {"households": int(hh) if hh else None,
                    "built": int(y) if y.isdigit() else None}
    amb = sum(1 for v in idx.values() if v is None)
    print(f"[poi] K-apt 단지 색인 {len(idx) - amb:,}건 ({files[-1].name} · 주소중복 {amb:,}건 제외)")
    return {k: v for k, v in idx.items() if v}


def main(argv=None):
    ap = argparse.ArgumentParser(description="시군구별 POI 조립")
    ap.add_argument("--keep-old", action="store_true",
                    help="기존 poi_*.json 을 지우지 않는다")
    a = ap.parse_args(argv)

    by_sgg: dict[str, dict] = defaultdict(
        lambda: {"school": [], "academy": [], "apartment": [],
                 "own": [], "competitor": [], "english_kinder": []})
    src: dict[str, str] = {}
    dropped = {"school": 0, "academy": 0, "apartment": 0}

    # ---- 학교 ----
    sch = read(PROC / "school.json")
    if sch:
        for sgg, lst in (sch.get("poi") or {}).items():
            by_sgg[sgg]["school"] = lst
        src["school"] = f"school.json (base_year={sch['meta'].get('base_year')})"
        print(f"[poi] 학교 {sum(len(v) for v in (sch.get('poi') or {}).values()):,}건")

    # ---- 학원 (NEIS 원본 + 지오코딩 캐시) ----
    # ⚠️ 분야 필터는 `collect_neis.py` 의 `REALM_KEEP` 를 **그대로 import** 한다.
    #    여기서 따로 정의하면 지도 마커 수와 지표 `academy_cnt` 가 갈라지고,
    #    상담 자리에서 "숫자랑 지도가 다르다"는 최악의 질문을 받는다.
    from collect_neis import is_target_realm, REALM_KEEP      # noqa: E402
    cache = (read(PROC / "geocode_cache.json") or {}).get("data") or {}
    n_aca, n_other = 0, 0
    for f in sorted(NEIS_RAW.glob("neis_*.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            if (r.get("REG_STTUS_NM") or "").strip() != "개원":
                continue
            if not is_target_realm(r):
                n_other += 1
                continue
            ad = (r.get("FA_RDNMA") or "").strip()
            g = cache.get(ad) if ad else None
            if not g:
                dropped["academy"] += 1
                continue
            by_sgg[g["adm_cd"][:5]]["academy"].append({
                "name": (r.get("ACA_NM") or "").strip(),
                "course": (r.get("LE_ORD_NM") or r.get("REALM_SC_NM") or "").strip(),
                "lat": g["lat"], "lng": g["lng"], "adm_cd": g["adm_cd"],
                "capacity": r.get("TOFOR_SMTOT"),
            })
            n_aca += 1
    if n_aca:
        src["academy"] = (f"NEIS acaInsTiInfo + SGIS 지오코딩 · 분야 {sorted(REALM_KEEP)}")
        print(f"[poi] 입시·보습 학원 {n_aca:,}건 "
              f"(다른 분야 제외 {n_other:,} · 좌표없음 탈락 {dropped['academy']:,})")

    # ---- 아파트 단지 ----
    cx = read(PROC / "rtms_complex.json")
    if cx:
        kidx = kapt_by_road()                 # C-5 — 단지별 세대수 (도로명주소 매칭)
        n_hh = 0
        # 2026-09-18 마커에 올리지 않는 단지 (관리자 결정)
        #   ① 도로명이 유실된 단지(road 가 빈 값이거나 번호만) — 지오코딩이 **시군구 대표점**에 찍는다.
        #      광산구 한 점에 서로 다른 동 33개 단지가 포개져 있었다. 틀린 자리에 찍는 대신 뺀다.
        #   ② 같은 단지가 두 레코드로 갈린 것 — 마커가 겹쳐 두 개로 보인다. 하나로 합친다.
        #   ⚠️ 가격·세대수 **집계**는 apartment.json(거래 신고 법정동 기준)이 따로 들고 있어 영향이 없다.
        #      여기서 빠지는 것은 지도 마커와 「N개 단지」 표시뿐이다.
        #   ⚠️ 판정은 "한글이 있는가" 다. 도로명에는 숫자가 들어간다(양일1로·이화여대8길) —
        #      `[가-힣]+(로|길)` 같은 패턴으로 재면 정상 도로명 4,402건을 유실로 오판한다(실측).
        n_no_road, n_merged = 0, 0
        seen = {}
        for c in cx["data"]:
            if not re.search(r"[가-힣]", str(c.get("road") or "")):
                n_no_road += 1
                continue
            k = kidx.get(c["adm_cd"][:5] + "|" + "".join(str(c.get("road") or "").split()))
            key = (round(c["lat"], 6), round(c["lng"], 6), c["name"])
            prev = seen.get(key)
            if prev is not None:
                d_prev, d_cur = prev.get("deal_count") or 0, c.get("deal_count") or 0
                if d_prev + d_cur:   # 평균가는 **거래건수 가중평균** — 단순 평균은 거래가 많은 쪽을 희석한다
                    prev["avg_price"] = round(((prev.get("avg_price") or 0) * d_prev
                                               + (c.get("avg_price") or 0) * d_cur) / (d_prev + d_cur))
                prev["deal_count"] = d_prev + d_cur
                prev["households"] = prev.get("households") or (k or {}).get("households")
                prev["built"] = prev.get("built") or c["built"] or (k or {}).get("built")
                n_merged += 1
                continue
            if k:
                n_hh += 1
            rec = {
                "name": c["name"], "lat": c["lat"], "lng": c["lng"],
                "adm_cd": c["adm_cd"],
                # 세대수 원천은 K-apt 다 — RTMS 실거래에는 세대수 항목이 자체가 없다.
                # 못 붙은 단지는 null 로 둔다(0 으로 채우면 "세대수 0인 단지"가 된다).
                "households": (k or {}).get("households"),
                "avg_price": c["avg_price"], "built": c["built"] or (k or {}).get("built"),
                "deal_count": c["deal_count"],
            }
            seen[key] = rec
            by_sgg[c["adm_cd"][:5]]["apartment"].append(rec)
        n_kept = len(cx["data"]) - n_no_road - n_merged
        dropped["apartment_no_road"] = n_no_road
        dropped["apartment_merged"] = n_merged
        src["apartment"] = f"RTMS 실거래 단지 ({cx['meta'].get('period')}) + K-apt 세대수"
        print(f"[poi] 아파트 단지 {n_kept:,}건 "
              f"(세대수 결합 {n_hh:,}건 · {n_hh/max(n_kept,1)*100:.1f}%) "
              f"· 도로명 유실 제외 {n_no_road:,} · 중복 병합 {n_merged:,}")
    else:
        print("[poi] ! rtms_complex.json 이 없다 — 아파트 레이어 비어 있음 "
              "(collect_rtms.py 를 먼저 실행)")

    # ---- 자사 센터 · 경쟁사 · 시너지세권(영어유치원) ----
    # 상담 자리에서 가장 먼저 보는 레이어다. 없으면 지도가 "우리가 어디 있는지"를
    # 못 보여준다 → 파일이 없을 때 조용히 넘어가지 말고 안내를 남긴다.
    comp = read(PROC / "competitor.json")
    if comp:
        for r in comp["data"]:
            by_sgg[r["sgg_cd"]][r["category"]].append({
                "name": r["name"], "brand": r["brand"],
                "lat": r["lat"], "lng": r["lng"], "adm_cd": r["adm_cd"],
                "addr": r["addr_raw"], "first_seen": r.get("first_seen"),
                "brand_target": r.get("brand_target"),
            })
        cm = comp["meta"]
        src["competitor"] = (f"경쟁사/자사 센터 ({cm.get('base_ym')} 기준, "
                             f"{'+'.join(sorted({v.split()[0] for v in cm['source'].values()}))})")
        print(f"[poi] 자사 {cm['by_category']['own']:,} · "
              f"경쟁사 {cm['by_category']['competitor']:,} · "
              f"시너지세권 {cm['by_category']['english_kinder']:,}건")
    else:
        print("[poi] ! competitor.json 이 없다 — 자사·경쟁사 레이어가 비어 있음 "
              "(load_competitor.py → build_competitor.py 를 먼저 실행)")

    if not by_sgg:
        print("[poi] 입력이 하나도 없다", file=sys.stderr)
        return 2

    # ---- 기존 더미 제거 ----
    if not a.keep_old:
        for old in PROC.glob("poi_*.json"):
            m = (read(old) or {}).get("meta") or {}
            if m.get("mode") == "sample" or m.get("sample") is True:
                old.unlink()
                print(f"[poi] 더미 삭제 {old.name}")

    for sgg, d in sorted(by_sgg.items()):
        obj = {
            "meta": {
                "sample": False,
                "updated": date.today().isoformat(),
                "sgg_cd": sgg,
                "source": src,
                "counts": {k: len(v) for k, v in d.items()},
                "dropped": dropped,
            },
            **d,
        }
        (PROC / f"poi_{sgg}.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    tot = {k: sum(len(v[k]) for v in by_sgg.values())
           for k in ("school", "academy", "apartment",
                     "own", "competitor", "english_kinder")}
    print(f"\n[poi] 시군구 {len(by_sgg)}개 파일 · {tot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
