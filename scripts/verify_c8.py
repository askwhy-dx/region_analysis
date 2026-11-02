# -*- coding: utf-8 -*-
"""verify_c8.py — 발행 게이트(C-8) 데이터 검증 — **읽기 전용**

`REVIEW.md` §3 전 항목 + 2026-08-03 신설 자사·경쟁사·시너지세권 축을 다시 계산한다.
산출물을 고치지 않으므로 언제든 돌려도 안전하고, 월 갱신
(`update_competitor.bat`) 뒤에 한 번 돌리는 것을 전제로 만들었다.

  python scripts/verify_c8.py          # FAIL 이 있으면 종료코드 1

⚠️ 이 검증은 **"조용히 빠지는 오류"** 를 잡는 것이 목적이다.
   지도에서 사라진 동·증발한 경쟁사·회색 코로플레스는 화면만 봐서는 안 보이고,
   그 화면이 그대로 가맹상담에 쓰인다.

⚠️ `KNOWN_ORPHANS` 는 **관리자가 인지하고 남겨 둔 예외**다(D-1 출장소 3건).
   여기에 코드를 추가하는 것으로 검증을 통과시키지 말 것 —
   새 orphan 이 생겼다면 그건 재생성이 깨진 것이다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"
G = ROOT / "data" / "geo"

# D-1 출장소 3건 — 원산·신수·삼랑진임천. 경계 폴리곤이 없어 지도에 못 올린다.
# 인구 2,582명(전국 0.005%). 해소 = 소속 동 합산(관리자 컨펌, DATA_CONTRACT §0-9)
# 또는 유니버스 제외. `build_metrics.hard_guard()` 의 예외와 같은 목록이다.
# D-1 출장소 3건(원산·신수·삼랑진임천).
# 2026-08-04 종결 = **행정동 유니버스에서 제외**(build_metrics.BRANCH_OFFICES).
# 인구는 시군구·시도에 남아 총인구 51,091,769명이 보존된다.
BRANCH_OFFICES = {"44180329", "48240890", "48270251"}
# 아래는 "경계에 없는데 metrics 에 남아 있는 코드"의 허용 목록이다. D-1 이 제외로
# 끝났으므로 비어 있는 게 정상이고, 다시 채워야 할 상황이 오면 그때가 재검토 시점이다.
KNOWN_ORPHANS: set[str] = set()

RESULT: list[tuple[str, str, str]] = []


def ck(cond, name, detail=""):
    RESULT.append(("PASS" if cond else "FAIL", name, detail))
    print(f"  {'[OK]' if cond else '[!!]'} {name}" + (f"  — {detail}" if detail else ""))


def warn(name, detail=""):
    RESULT.append(("WARN", name, detail))
    print(f"  [~~] {name}" + (f"  — {detail}" if detail else ""))


def rd(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> int:
    print("=" * 78)
    print("C-8 발행 게이트 검증 (데이터)")
    print("=" * 78)

    met, met_sgg, met_sido = rd(P / "metrics.json"), rd(P / "metrics_sgg.json"), rd(P / "metrics_sido.json")
    pop, apt = rd(P / "population.json"), rd(P / "apartment.json")
    comp = rd(P / "competitor.json")
    comp_adm, comp_sgg, comp_sido = (rd(P / "competitor_adm.json"), rd(P / "competitor_sgg.json"),
                                     rd(P / "competitor_sido.json"))
    if not met or not comp:
        print("[FATAL] metrics.json / competitor.json 이 없다", file=sys.stderr)
        return 2

    geo_adm, geo_sgg = set(), set()
    for f in G.glob("adm_*.geojson"):
        geo_sgg.add(f.stem.split("_")[1])
        for ft in json.loads(f.read_text(encoding="utf-8"))["features"]:
            geo_adm.add(ft["properties"]["code"])
    geo_sido = {c[:2] for c in geo_sgg}

    MK, PK = set(met["data"]), set(pop["data"])

    # ---------------------------------------------------------------- §3-1/2
    print("\n[§3-1] 코드 집합 정합")
    print(f"  population {len(PK):,} · metrics {len(MK):,} · geo {len(geo_adm):,}")
    orphan = (MK - geo_adm) - KNOWN_ORPHANS
    ck(not orphan, "metrics − geo (지도에 못 올리는 동)",
       f"신규 {len(orphan)}건 {sorted(orphan)[:5]} · 기지 예외 {len(MK & KNOWN_ORPHANS)}건(D-1)")
    if MK & KNOWN_ORPHANS:
        warn("D-1 출장소 잔존", f"{sorted(MK & KNOWN_ORPHANS)} — 지도 미표시, 관리자 결정 대기")
    ck(not (geo_adm - MK), "geo − metrics (지도에서 회색)", f"{len(geo_adm - MK)}건")
    # D-1 종결(2026-08-04) — 출장소는 **행정동 목록에서만** 빠진다. 그래서
    # metrics 키 == population 키 − 출장소 가 새 불변식이다.
    # ⚠️ 이 검사를 "차 3건 정도는 봐준다"로 느슨하게 만들면 안 된다. 정확히 그 3개가
    #    빠졌고 다른 건 안 빠졌다는 것까지 확인해야 진짜 검증이다.
    ck(MK == PK - BRANCH_OFFICES, "metrics 키 == population 키 − 출장소 3건",
       f"metrics {len(MK):,} · population {len(PK):,} · "
       f"뜻밖의 차 {sorted((PK ^ MK) - BRANCH_OFFICES)[:5]}")
    ck(not (MK & BRANCH_OFFICES), "출장소 3건 행정동 목록에서 제외됨 (D-1)",
       f"잔존 {sorted(MK & BRANCH_OFFICES)}")

    print("\n[§3-2] 시군구 / 시도")
    ck(set(met_sgg["data"]) == geo_sgg, "metrics_sgg == geo_sgg", f"차 {len(set(met_sgg['data']) ^ geo_sgg)}건")
    ck(set(met_sido["data"]) == geo_sido, "metrics_sido == geo_sido", f"{sorted(set(met_sido['data']) ^ geo_sido)}")
    ck(len(geo_sido) == 16 and "12" in geo_sido, "시도 16개 + 전남광주통합(12)", f"{len(geo_sido)}개")
    legacy = {k for k in MK | set(met_sgg["data"]) | set(met_sido["data"]) if k.startswith(("29", "46"))}
    ck(not legacy, "구코드(29/46) 잔재", f"{len(legacy)}건")

    # ---------------------------------------------------------------- §3-3
    print("\n[§3-3] 총인구 보존 · 롤업")
    t_a = sum(v.get("population") or 0 for v in met["data"].values())
    t_s = sum(v.get("population") or 0 for v in met_sgg["data"].values())
    t_d = sum(v.get("population") or 0 for v in met_sido["data"].values())
    # 출장소 인구는 행정동 목록에서만 빠졌을 뿐 시군구·시도에는 그대로 살아 있다.
    # 그래서 "행정동 합 + 출장소 = 시군구 합" 이 성립해야 한다. 이게 안 맞으면
    # 출장소가 아니라 **다른 무언가가 사라진 것**이다.
    branch_pop = sum((pop["data"].get(c) or {}).get("total") or 0 for c in BRANCH_OFFICES)
    print(f"  행정동 {t_a:,} (+출장소 {branch_pop:,}) · 시군구 {t_s:,} · 시도 {t_d:,}")
    ck(t_a + branch_pop == t_s == t_d, "3레벨 총인구 일치 (출장소 보정 후)",
       f"행정동+출장소 {t_a + branch_pop:,} vs 시군구 {t_s:,} vs 시도 {t_d:,}")

    for src, dst, cut, nm in ((met, met_sgg, 5, "행정동 → 시군구"), (met_sgg, met_sido, 2, "시군구 → 시도")):
        roll: dict[str, int] = {}
        for k, v in src["data"].items():
            roll[k[:cut]] = roll.get(k[:cut], 0) + (v.get("population") or 0)
        if src is met:            # 행정동 롤업에는 빠진 출장소를 되돌려 놓고 비교한다
            for c in BRANCH_OFFICES:
                roll[c[:cut]] = roll.get(c[:cut], 0) + ((pop["data"].get(c) or {}).get("total") or 0)
        bad = [k for k, v in roll.items() if v != (dst["data"].get(k, {}).get("population") or 0)]
        ck(not bad, f"{nm} 롤업", f"불일치 {len(bad)}건 {bad[:5]}")

    bad = [k for k, v in met["data"].items()
           if v.get("school_age") is not None
           and (pop["data"].get(k, {}) or {}).get("school_age") is not None
           and v["school_age"] != pop["data"][k]["school_age"]]
    ck(not bad, "metrics.school_age == population.school_age", f"불일치 {len(bad)}건")

    # ---------------------------------------------------------------- §3-4
    print("\n[§3-4] 파생 지표 전건 재계산")
    bad, zero = [], 0
    for k, v in met["data"].items():
        a, s, r = v.get("academy_cnt"), v.get("school_age"), v.get("pupils_per_academy")
        if not a:
            zero += 1
            if r is not None:
                bad.append(k)
            continue
        if s is not None and (r is None or abs(r - s / a) > 0.15):
            bad.append(k)
    ck(not bad, "pupils_per_academy = school_age / academy_cnt (±0.15)",
       f"불일치 {len(bad)}건 · 학원 0인 동 {zero}곳은 null")

    bad = [k for k, v in met["data"].items()
           if (v.get("avg_price_per_py") or None) != ((apt["data"].get(k) or {}).get("avg_price_per_py") or None)]
    ck(not bad, "metrics.avg_price_per_py == apartment.avg_price_per_py", f"불일치 {len(bad)}건")

    # ⚠️ 아래 둘은 **실측 없음**이지 결손이 아니다 (B-8: 0 은 결측이 아니다)
    print(f"  ※ 실측 없음: 평당가 {sum(1 for v in met['data'].values() if v.get('avg_price_per_py') is None)}동 · "
          f"초·중 재학생 {sum(1 for v in met['data'].values() if v.get('school_students') is None)}동")

    # ---------------------------------------------------------------- C-5 세대수
    print("\n[C-5] 아파트 세대수 (K-apt 조인)")
    kapt = rd(P / "kapt.json")
    km = kapt.get("meta") or {}
    # ① 수집이 끝났는가. partial 인 채로 발행하면 세대수가 **적게** 나오고,
    #    그 방향의 오류는 화면에서 "아파트 적은 동"으로 보여 눈에 띄지 않는다.
    ck(km.get("partial") is False and km.get("progress") == 1.0,
       "K-apt 수집 완료 (partial=false)", f"진행률 {km.get('progress')}")
    ck(km.get("collected") == km.get("total_complexes"),
       "단지 전수 수집", f"{km.get('collected'):,}/{km.get('total_complexes'):,}")
    # ② 3레벨 세대수 합계가 같은가 (롤업이 깨지면 시군구 화면만 조용히 틀린다)
    tot = {lv: sum(v.get("households", 0) for v in (kapt.get(k) or {}).values())
           for lv, k in (("adm", "data"), ("sgg", "sgg"), ("sido", "sido"))}
    ck(len(set(tot.values())) == 1, "3레벨 아파트 세대수 일치",
       " vs ".join(f"{lv} {n:,}" for lv, n in tot.items()))
    # ③ metrics 가 kapt.json 을 그대로 옮겼는가 (SSOT 확인 — 조인이 낡으면 여기가 갈린다)
    bad = [k for k, v in met["data"].items()
           if (v.get("apt_households") or None)
           != ((kapt["data"].get(k) or {}).get("households") or None)]
    ck(not bad, "metrics.apt_households == kapt.households", f"불일치 {len(bad)}건 {bad[:5]}")
    # ④ 이름이 겹치면 반드시 잘못 나눈다 — households(주민등록)와 apt_households(아파트)
    bad = [k for k, v in met["data"].items()
           if v.get("households") is not None
           and v["households"] != ((pop["data"].get(k) or {}).get("households"))]
    ck(not bad, "metrics.households == 주민등록 세대수 (아파트 세대수 혼입 없음)",
       f"불일치 {len(bad)}건 {bad[:5]}")
    # ⑤ 100% 초과 동은 지우지 말고 **세어서** 드러낸다 (경계 원천 문제, E-4)
    over = [k for k, v in met["data"].items() if v.get("apt_ratio_suspect")]
    if over:
        warn("아파트 거주 비율 100% 초과",
             f"{len(over)}개 동 — 경계 원천의 분동 반영 지연(E-4) · 색·순위에서 제외됨")
    else:
        ck(True, "아파트 거주 비율 100% 초과 없음")
    # 플래그 자체가 사라지면(필드 오타·리팩터링) 위 경고가 조용히 꺼진다 → 존재를 확인한다
    ck(any("apt_ratio_suspect" in v for v in met["data"].values()),
       "apt_ratio_suspect 필드 존재")

    # ---------------------------------------------------------------- sample
    print("\n[A-7 ④] meta.sample 전수 검사")
    files = sorted(P.glob("*.json"))
    bad = []
    for f in files:
        j = rd(f)
        m = j.get("meta") if isinstance(j, dict) else None
        if not isinstance(m, dict):
            bad.append((f.name, "meta 없음"))
        elif m.get("sample") is True or m.get("mode") == "sample":
            bad.append((f.name, "sample"))
    ck(not bad, f"data/processed {len(files)}개 전부 sample=false", f"{bad[:5]}")

    # ---------------------------------------------------------------- 좌표 ↔ 동 일치
    # 2026-08-12 신설. 지도 **마커는 좌표**에 찍히고 카드 **숫자는 adm_cd** 로 센다.
    # 둘이 어긋나면 "선택한 동에 없는 시설이 세어지는" 모양이 되는데, 지금까지 이걸
    # 검사하는 곳이 없었다 — 광주 하남동 경쟁사 건은 **사람이 현장에서 발견**했다.
    #
    # ⚠️ 어긋남이 전부 오류는 아니다. E-4 심판이 낡은 폴리곤을 이기고 옮긴 건은
    #    좌표와 다른 게 **정상**이다. 그래서 심판이 손댄 좌표는 예외로 빼고,
    #    **설명되지 않는 어긋남만** 잡는다.
    print("\n[좌표 정합] POI 좌표 → 행정동 재판정")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from admlookup import AdmIndex                      # noqa: E402
        idx = AdmIndex.load(verbose=False)
    except Exception as e:                                  # noqa: BLE001
        warn("좌표 정합 검사 생략", f"{type(e).__name__}: {e}")
        idx = None
    if idx:
        gc = (rd(P / "geocode_cache.json") or {}).get("data") or {}
        # 심판이 옮긴 좌표 — 여기서 어긋나는 것은 의도된 결과다
        arb = {(round(v["lat"], 6), round(v["lng"], 6)) for v in gc.values()
               if v and v.get("arbiter") == "juso" and v.get("lat") and v.get("lng")}
        files = [(f.stem.replace("poi_", ""), f) for f in sorted(P.glob("poi_*.json"))
                 if f.stem.replace("poi_", "").isdigit()]
        files += [(f.stem, f) for f in sorted((P / "poi_childcare").glob("*.json"))]
        n_pts, mism, by_kind = 0, {}, {}
        for sgg, f in files:
            d = rd(f) or {}
            groups = d.items() if isinstance(d, dict) else [("childcare", d)]
            for kind, rows in groups:
                if not isinstance(rows, list):
                    continue
                for r in rows:
                    if not isinstance(r, dict) or not r.get("lat") or not r.get("adm_cd"):
                        continue
                    n_pts += 1
                    got = idx.lookup(r["lng"], r["lat"], sgg_hint=sgg)
                    if got and got != r["adm_cd"]:
                        if (round(r["lat"], 6), round(r["lng"], 6)) in arb:
                            continue                        # 심판이 옮긴 건 — 정상
                        by_kind[kind] = by_kind.get(kind, 0) + 1
                        mism.setdefault(kind, []).append(
                            f"{r.get('name')}({r['adm_cd']}→{got})")
        # 학교는 지오코딩 캐시를 안 쓴다(원천 CSV 좌표를 직접 PIP). 그래서 심판 이력이
        # 캐시에 없어 예외 목록으로 뺄 수가 없다 → school.json 이 보고한 교정 건수까지는 허용한다.
        sch_moved = ((rd(P / "school.json") or {}).get("meta", {})
                     .get("report", {}).get("arbiter_e4", {}).get("moved", 0))
        sch_bad = by_kind.get("school", 0)
        other = {k: v for k, v in by_kind.items() if k != "school"}
        ex = [f"{k}:{v}" for k in other for v in mism.get(k, [])[:2]][:3]
        ck(not other, f"POI {n_pts:,}건 좌표 = 저장된 행정동 (학교 제외)",
           f"불일치 {sum(other.values())}건 {other} · 예: {ex}")
        ck(sch_bad <= sch_moved, "학교 좌표 불일치 ≤ 심판 교정 건수",
           f"{sch_bad}건 / 심판 교정 {sch_moved}건")

    # ---------------------------------------------------------------- 주소 ↔ 마킹 (2026-09-18 신설)
    # 2026-09-18 실측: 학교알리미 CSV 가 **같은 이름의 다른 학교에 같은 좌표**를 준다.
    # 구리 인창중·인창고가 서울 인창중·인창고 좌표에 얹혀 서대문구 충현동에 찍혔고,
    # 충현동 학생수에 1,486명이 더해진 채로 상담 화면에 나가고 있었다.
    # 지오코딩 실패와 달리 **에러가 없고 좌표도 국내 범위 안**이라 어떤 기존 검사에도 안 걸렸다.
    print()
    print("[주소↔마킹] 같은 자리에 겹쳐 찍힌 POI")
    sch = []
    for f in sorted(P.glob("poi_*.json")):
        if not f.stem.replace("poi_", "").isdigit():
            continue
        for r in (rd(f) or {}).get("school", []):
            sch.append(r)
    seen = {}
    dup_same = []
    for r in sch:
        k = (round(r["lat"], 6), round(r["lng"], 6), r.get("name"), r.get("type"))
        if k in seen:
            dup_same.append(f"{r.get('type')} {r.get('name')} ({r['lat']},{r['lng']})")
        seen[k] = True
    ck(not dup_same, f"학교 {len(sch):,}개 — 같은 이름·같은 좌표로 두 번 찍힌 학교 없음",
       f"{len(dup_same)}건 {dup_same[:3]}")

    # 아파트 마커 : 도로명이 유실된 단지는 **올리지 않는다**(2026-09-18 관리자 결정).
    #   road 가 빈 값이거나 번호만인 단지는 지오코딩이 시군구 대표점에 찍는다 —
    #   광산구 한 점에 서로 다른 동 33개 단지가 포개져 있었다. 틀린 자리에 찍는 대신 뺀다.
    #   같은 단지가 두 레코드로 갈린 것도 하나로 합친다.
    #   ⚠️ 가격·세대수 집계는 apartment.json 이 따로 들고 있어 이 제외와 무관하다.
    cxr = rd(P / "rtms_complex.json")
    apt = []
    for f in sorted(P.glob("poi_*.json")):
        if f.stem.replace("poi_", "").isdigit():
            apt += (rd(f) or {}).get("apartment", [])
    if cxr:
        want = {}
        for c in cxr.get("data", []):
            if re.search(r"[가-힣]", str(c.get("road") or "")):
                want[(round(c["lat"], 6), round(c["lng"], 6), c["name"])] = 1
        ck(len(apt) == len(want),
           f"아파트 마커 {len(apt):,}개 == 도로명 있는 단지의 서로 다른 (좌표·단지명) 수",
           f"marker {len(apt):,} vs expect {len(want):,} · 원천 {len(cxr.get('data', [])):,}")
    seen_a, dup_a = set(), []
    for r in apt:
        k = (round(r["lat"], 6), round(r["lng"], 6), r.get("name"))
        if k in seen_a:
            dup_a.append(f"{r.get('name')} {k[0]},{k[1]}")
        seen_a.add(k)
    ck(not dup_a, "같은 이름·같은 좌표로 두 번 찍힌 아파트 단지 없음", f"{len(dup_a)}건 {dup_a[:3]}")

    # 아파트 단지 : 원천 주소의 시군구와 **좌표가 찍힌 시군구**가 다른 단지.
    # ⚠️ adm_cd(집계용)는 거래 신고의 법정동에서 오므로 좌표 PIP 와 원래 다를 수 있다
    #    (2026-09-18 실측 525건). 그건 설계이므로 보지 않는다 — 여기서 보는 건 **마커 자리**다.
    # ⚠️ 원천 주소 쪽이 틀린 곳도 있어(NEIS 학원 창원 사례) FAIL 이 아니라 WARN 으로 센다.
    cxr = rd(P / "rtms_complex.json")
    if idx and cxr:
        names = {}
        for code, v in (met_sgg.get("data") or {}).items():
            nm = v.get("name") or ""
            names.setdefault(nm, []).append(code)
            m = re.match(r"^(.+?시)(.+구)$", nm)
            if m:
                names.setdefault(m.group(2), []).append(code)
                names.setdefault(m.group(1) + " " + m.group(2), []).append(code)
        off, undec = [], 0
        # 마커에 올라가지 않는 단지(도로명 유실)는 셀 이유가 없다 — 지도에 없다
        for r in cxr.get("data", []):
            if not re.search(r"[가-힣]", str(r.get("road") or "")):
                continue
            a = (r.get("sgg_str") or "").strip()
            got = idx.lookup(r["lng"], r["lat"]) if a else None
            if not got:
                undec += 1
                continue
            cands = {c for nm, cs in names.items() if nm and nm in a for c in cs if c[:2] == got[:2]}
            if not cands:
                undec += 1
                continue
            if got[:5] not in cands:
                off.append(f"{r.get('name')} | {a}")
        if off:
            warn(f"아파트 마커 {len(apt):,}개 — 주소와 다른 시군구에 찍힌 것",
                 f"{len(off)}건 (판정 불가 {undec}건) · 예: {off[:2]}")
        else:
            ck(True, f"아파트 마커가 전부 원천 주소의 시군구 안 (판정 불가 {undec}건)")

    # ---------------------------------------------------------------- 어린이집 축
    # 2026-08-12 신설. 포털 arcode 가 행안부 표준코드와 다른 시군구가 있어 표준코드로 물으면
    # HTTP 200 + INFO-200 이 온다 — **"어린이집 0개"와 응답이 똑같다.** 그 상태로 발행하면
    # 그 지역이 화면에서 '어린이집 없는 상권'이 된다(제주도 전체가 그랬다).
    print("\n[어린이집] 전국 커버리지")
    cc = rd(P / "childcare.json")
    # 게이트의 뜻: **부분수집분이 지표에 섞여 나가지 않는다.**
    # 수집이 덜 됐다는 사실 자체는 발행 사유가 아니다 — build_metrics 가 축을 통째로
    # 빼면 화면에 '어린이집 0개'가 안 나가므로 안전하다. 막아야 하는 건 '섞여서 나가는' 경우다.
    merged = bool(((met.get("meta") or {}).get("axes") or {}).get("childcare"))
    if cc:
        ccm = cc.get("meta") or {}
        un = ccm.get("unresolved_sgg") or {}
        if ccm.get("partial") and not merged:
            warn("어린이집 축 대기", f"미수집 {len(un)}개({', '.join(list(un.values())[:6])})"
                                 f" → metrics 에서 제외됨. 화면에 0 이 안 나간다")
        else:
            ck(not ccm.get("partial"), "childcare 부분수집분이 지표에 섞이지 않음",
               f"미해결 {len(un)}개: {', '.join(list(un.values())[:8])} · 지표 반영 {merged}")
        # 시군구 단위 실측 — meta 를 안 믿고 값으로도 본다. 경계 넘어온 1~2건이
        # 0 을 면하게 만들 수 있어 **인구 대비 지나치게 적은 곳**까지 같이 잡는다.
        sgg_kids = {}
        for adm, v in (cc.get("data") or {}).items():
            sgg_kids[adm[:5]] = sgg_kids.get(adm[:5], 0) + (v.get("daycare_kids") or 0)
        thin = []
        for c, v in ((met_sgg["data"] or {}) if merged else {}).items():
            pre = v.get("preschool")            # 만0~5세 주민등록 인구
            if not pre:
                continue
            kids = sgg_kids.get(c, 0)
            if kids < pre * 0.05:               # 유아 인구의 5% 도 안 잡히면 수집 실패로 본다
                thin.append(f"{c} {v.get('name')}({kids}/{pre})")
        # 축이 빠진 상태에서는 이 검사가 의미 없다(전 시군구가 0이라 전부 '희박'이 된다).
        if merged:
            ck(not thin, f"시군구 {len(met_sgg['data'])}개 어린이집 커버리지",
               f"희박 {len(thin)}개: {thin[:6]}")
    else:
        warn("childcare.json 없음", "어린이집 축 미수집")

    # ------------------------------------------------- 12개월 인구 증감 (개편 구간)
    # 2026-08-13 신설. 증감률은 12개월 전과 지금을 나눈 값이라 그 사이 **코드가 신설되면**
    # 산수가 무의미하다. 실제로 검단구 +49.2% · 영종구 +48.3% · 음성군 +30.1% 가
    # 화면에 떠 있었다(전부 분구·읍 승격의 점프). 상담 자리에서 그대로 근거로 쓰인다.
    print("\n[인구] 12개월 증감 — 행정구역 개편 구간")
    zero_adm = {c for c, v in (pop["data"] or {}).items() if 0 in (v.get("trend12") or [])}
    leaked = sorted(c for c in zero_adm if (met["data"].get(c) or {}).get("trend_pct") is not None)
    ck(not leaked, "코드가 신설된 행정동은 증감률을 내지 않는다",
       f"값이 남은 곳 {len(leaked)}개: {leaked[:6]}")
    up = {c[:5] for c in zero_adm} | {c[:2] for c in zero_adm}
    roll = sorted(c for c in up
                  if ((met_sgg["data"].get(c) or met_sido["data"].get(c) or {}).get("trend_pct")) is not None)
    ck(not roll, "그 상위 시군구·시도도 같이 비운다", f"값이 남은 곳 {len(roll)}개: {roll[:6]}")
    both = [c for d in (met, met_sgg, met_sido) for c, v in d["data"].items()
            if v.get("trend_break") and v.get("trend_pct") is not None]
    ck(not both, "trend_break 와 값이 동시에 있지 않다", f"{len(both)}건")
    # ⚠️ 반대 방향도 본다 — **급증을 다 지우면 안 된다.** 광명2동(재건축 입주)은
    #    개편이 아니라 실측이라 값이 남아 있어야 한다. 여기가 비면 규칙이 과하게 잡은 것이다.
    kept = [c for c, v in met["data"].items()
            if not v.get("trend_break") and (v.get("trend_pct") or 0) > 20]
    ck(bool(kept), "개편이 아닌 실제 급증은 남아 있다", f"{len(kept)}개 동 (예: {kept[:3]})")

    # ---------------------------------------------------------------- 경쟁사 축
    print("\n[G/A-13] 자사 · 경쟁사 · 시너지세권")
    cm, data = comp["meta"], comp["data"]
    ck(cm["geocode"]["rate"] == 1.0, "지오코딩 100%", f"{cm['geocode']['ok']}/{len(data)}")
    # 번호 유실 = 좌표는 나왔지만 다른 건물·동 대표점 (build_competitor 하드가드, --no-guard 빌드도 여기서 막는다)
    if "imprecise" in cm["geocode"]:
        ck(not cm["geocode"]["imprecise"], "지오코딩 번호 유실 0건", f"{cm['geocode']['imprecise'][:5]}")
    else:
        warn("지오코딩 번호 유실 검사 전 산출물", "build_competitor.py 재실행 필요")
    ck(len(data) == sum(cm["counts"].values()), "지점 합계 == 브랜드별 합", f"{len(data)}건")
    ck(set(comp_adm["data"]) <= MK, "competitor_adm ⊆ metrics", f"orphan {len(set(comp_adm['data']) - MK)}건")
    ck(set(comp_sgg["data"]) <= geo_sgg, "competitor_sgg ⊆ geo_sgg")
    ck(set(comp_sido["data"]) <= geo_sido, "competitor_sido ⊆ geo_sido")

    own_brands = cm.get("own_brands") or []
    own = [r for r in data if r["category"] == "own"]
    ck(len(own_brands) >= 1 and len(own) == cm["by_category"]["own"], "자사 합산",
       f"{len(own)}건 = " + " + ".join(f"{b} {sum(1 for r in own if r['brand']==b)}" for b in own_brands))
    ck(cm.get("category_label", {}).get("english_kinder") == "시너지세권", "표시명 = 시너지세권")

    for lvl, obj in (("adm", comp_adm), ("sgg", comp_sgg), ("sido", comp_sido)):
        tot = sum(v["total"] for v in obj["data"].values())
        ck(tot == len(data), f"competitor_{lvl} 합계 == 지점수", f"{tot}")

    oob = [r["name"] for r in data if not (33.0 <= r["lat"] <= 38.7 and 124.5 <= r["lng"] <= 131.9)]
    ck(not oob, "좌표 국내 범위", f"{len(oob)}건")

    poi_files = list(P.glob("poi_*.json"))
    tot_poi = {"own": 0, "competitor": 0, "english_kinder": 0}
    missing = []
    for f in poi_files:
        j = rd(f)
        for k in tot_poi:
            (tot_poi.__setitem__(k, tot_poi[k] + len(j[k])) if k in j else missing.append((f.name, k)))
    ck(not missing, f"poi_*.json {len(poi_files)}개에 3개 키 존재", f"{missing[:3]}")
    ck(all(tot_poi[k] == cm["by_category"][k] for k in tot_poi),
       "POI 재조립 건수 == competitor.json", f"{tot_poi}")

    hist = (rd(P / "competitor_history.json") or {}).get("data") or {}
    act = [h for h in hist.values() if h.get("status") == "active"]
    ck(len(act) == len(data), "이력 active == 현재 지점수", f"{len(act)} vs {len(data)}")
    ck(not [k for k, h in hist.items() if h.get("status") == "active" and h.get("closed_at")],
       "active 인데 closed_at 남은 지점")

    if cm.get("pending"):
        warn("자료 대기 축", ", ".join(p["name"] for p in cm["pending"]))

    # ---------------------------------------------------------------- 요약
    fails = [r for r in RESULT if r[0] == "FAIL"]
    warns = [r for r in RESULT if r[0] == "WARN"]
    print("\n" + "=" * 78)
    print(f"판정 — PASS {sum(1 for r in RESULT if r[0]=='PASS')} · FAIL {len(fails)} · WARN {len(warns)}")
    for _, n, d in fails:
        print(f"  [!!] {n} — {d}")
    for _, n, d in warns:
        print(f"  [~~] {n} — {d}")
    print("=" * 78)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
