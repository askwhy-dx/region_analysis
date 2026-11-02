# -*- coding: utf-8 -*-
"""
지역분석 — 종합 지표 조립 (담당: 팀원3, 최종 조립 단계)

다른 팀원 산출물을 읽어 DATA_CONTRACT 2-6 의 metrics 3종을 만든다.

  입력 (없으면 해당 지표 null, 크래시하지 않음)
    data/processed/population*.json   팀원2 → school_age, trend_pct, households
    data/processed/poi_<sgg>.json     팀원4 → academy_cnt, school_cnt
    data/processed/apartment*.json    팀원3 → avg_price_per_py, avg_price, new_ratio
    data/processed/code_map.json      팀원1 → 지역명·유니버스

  출력
    data/processed/metrics.json       행정동
    data/processed/metrics_sgg.json   시군구
    data/processed/metrics_sido.json  시도

점수 산식은 docs/SCORE.md 가 SSOT. 이 파일은 그 구현이다.
입력이 나중에 채워지면 **그냥 재실행**하면 빈 지표가 메워진다.

사용법:
  python build_metrics.py
  python build_metrics.py --quiet
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"

# ── D-1 출장소 3건 (원산·신수·삼랑진임천) ─────────────────────────────────
# 행정동 **하위** 단위라 경계에 독립 폴리곤이 없다. 주민등록 통계만 이들을 독립 행으로
# 내보내서, 지도엔 못 뜨는데 목록엔 name:null 무명 행으로 남아 있었다.
# 2026-08-04 결정 = **행정동 유니버스에서 제외.** 인구 2,582명(전국 0.005%)은
# population_sgg/sido 에 이미 소속 시군구 몫으로 들어 있어 총인구는 보존된다.
BRANCH_OFFICES = {"44180329", "48240890", "48270251"}

# ═══════════════════════════════════════════════════════════════════════
# 🚫 점수제도 폐기 (2026-07-31, 관리자 결정)
#
# 종합점수(score) · 등급(grade) · coverage · confidence · score_parts 산출을
# **전부 제거**했다. 이 파일은 이제 **실측 지표만** 조립한다.
#
#   폐기 이유 — 모집단을 전국으로 고쳐도(B-4) "학원이 적은 곳이 높게 나온다"는
#   구도가 남았고(평택시 85.5 S > 강남구 61.9 B), 이는 데이터가 아니라
#   산식 정의의 문제였다. 가맹상담 자료에서 방어할 수 없다고 판단.
#
#   재설계는 나중 작업이다 → docs/TODO.md **F-1**
#   폐기된 산식의 전문(가중치·게이트·등급컷·근거)은 docs/SCORE.md 에 보존돼 있다.
#   ⚠️ 되살릴 때 SCORE.md 를 그대로 복원하지 말 것 — 폐기 사유가 산식 자체에 있다.
# ═══════════════════════════════════════════════════════════════════════


# ----------------------------------------------------------------- 유틸

# 콘솔이 cp949 면 '—' 같은 문자에서 print 가 죽는다. 산출물은 이미 다 쓴 뒤라
# 파일은 멀쩡한데 **종료코드만 1** 이 되어 배치가 "실패"로 보인다(2026-08-04 실제 발생).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUIET = False


def log(msg: str) -> None:
    if not QUIET:
        print(msg, flush=True)


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"  ! {path.name} 읽기 실패 → 없는 것으로 처리: {e}")
        return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  wrote {path.relative_to(ROOT)}  ({path.stat().st_size/1024:.0f} KB)")


# ----------------------------------------------------------------- 입력 로드

def load_population(level: str) -> dict:
    fn = {"adm": "population.json", "sgg": "population_sgg.json",
          "sido": "population_sido.json"}[level]
    d = read_json(PROCESSED / fn) or {}
    return {"data": d.get("data") or {}, "meta": d.get("meta") or {}, "file": fn}


def load_apartment(level: str) -> dict:
    fn = {"adm": "apartment.json", "sgg": "apartment_sgg.json",
          "sido": "apartment_sido.json"}[level]
    d = read_json(PROCESSED / fn) or {}
    return {"data": d.get("data") or {}, "meta": d.get("meta") or {}, "file": fn}


def load_kapt(level: str) -> dict:
    """K-apt 아파트 세대수 (C-5, 2026-08-05).

    ⚠️ **`kapt.json` 이 아파트 세대수의 SSOT 다.** `apartment.json` 에 조인해 넣지 않았다.
       ① `apartment.json` 은 `collect_rtms.py` 가 매달 통째로 다시 쓴다 — 조인 결과를
          거기 얹어 두면 다음 수집에서 **조용히 null 로 되돌아가고**, 세대수가 적게 나오는
          오류는 화면에서 "아파트 적은 동"으로 보여 눈에 띄지 않는다.
       ② `apartment.json` 은 **24개월 매매가 있는 동만** 담는다. 아파트는 있는데 거래가
          없던 동 **19곳(8,058세대, 예: 정발산동 1,705)** 이 통째로 빠진다.
       그래서 metrics 가 kapt.json 을 직접 읽는다. 조인 산출물이 낡는 상태 자체를 없앤다.

    ⚠️ 진행률이 100%가 아니면(`meta.partial`) 세대수는 **과소계상**이다 → 값을 내보내지 않는다.
    """
    d = read_json(PROCESSED / "kapt.json") or {}
    key = {"adm": "data", "sgg": "sgg", "sido": "sido"}[level]
    meta = d.get("meta") or {}
    if meta.get("partial"):
        print(f"[metrics] ⚠️ kapt.json 이 수집 중({meta.get('progress')}) "
              f"— 아파트 세대수 지표를 비운다(과소계상 방지)")
        return {"data": {}, "meta": meta, "file": "kapt.json"}
    return {"data": d.get(key) or {}, "meta": meta, "file": "kapt.json"}


def load_national() -> dict:
    """전국 단위 산출물(학교·학원)을 읽어 종류별 전국 카운트를 만든다.

    2026-07-31 이전에는 POI 가 4개 시군구(1.6%)뿐이라 경쟁강도 백분위의 모집단이 n=4 였다
    (서초구 59.9(B) < 평택시 91.8(S) 같은 방어 불가 순위의 원인, REVIEW C-4).
    학교(C-1)·학원(B-3)이 전국화되면서 여기가 전국 모집단이 된다 → **B-4**.

    반환: {kind: {"adm": {adm_cd: n}, "extra": {adm_cd: {...}}, "national": bool}}
    """
    out = {}

    sch = read_json(PROCESSED / "school.json")
    if sch and sch.get("data") and not (sch.get("meta") or {}).get("sample"):
        out["school"] = {
            "adm": {k: v.get("school_cnt", 0) for k, v in sch["data"].items()},
            "extra": {k: {"school_students": v.get("students"),
                          "students_elem": v.get("students_elem"),
                          "students_mid": v.get("students_mid"),
                          "students_high": v.get("students_high"),
                          "kinder_cnt": v.get("kinder_cnt"),
                          "kinder_kids": v.get("kinder_kids"),
                          "students_age_unassigned": v.get("students_age_unassigned"),
                          **{f"students_age_{age}": v.get(f"students_age_{age}", 0)
                             for age in range(3, 18)}}
                      for k, v in sch["data"].items()},
            "national": True,
            "source": "school.json",
            "base_year": (sch.get("meta") or {}).get("base_year"),
        }

    # 어린이집 (정보공개포털 cpmsapi030) — 2026-08-12 합류.
    # ⚠️ 여기서는 `daycare_*` 이름으로만 내보낸다. **school 과 같은 키를 쓰면 안 된다** —
    #    아래 assemble() 의 extra 병합이 `rec[c][ek] = ev` **대입**이라, 같은 이름을 두 kind 가
    #    내보내면 뒤에 오는 쪽이 앞을 덮어써 유치원 원아가 통째로 사라진다(합산이 아니다).
    #    연령 합산은 assemble() 안에서 명시적으로 한다.
    cc = read_json(PROCESSED / "childcare.json")
    # [2026-08-12] **부분수집이면 아예 섞지 않는다.**
    # 246개 시군구는 맞지만 6개가 통째로 비어 있어, 그대로 내보내면 제주 전역이
    # '어린이집 0개'로 보인다 — 0 은 화면에서 실측처럼 읽힌다. 축 하나를 통째로 빼는 쪽이
    # 틀린 0 을 내보내는 것보다 낫다(관리자 결정 2026-08-12).
    # 수집이 끝나면 이 분기가 저절로 풀려 다음 빌드부터 합류한다 — 수동 전환이 없다.
    if cc and (cc.get("meta") or {}).get("partial"):
        un = (cc["meta"].get("unresolved_sgg") or {})
        print(f"[metrics] ⛔ childcare 부분수집({len(un)}개 시군구 미수집) — **이번 빌드에서 제외**한다. "
              f"{', '.join(list(un.values())[:6])}")
        cc = None
    if cc and (cc.get("meta") or {}).get("partial"):
        # kapt 과 달리 **비우지 않는다.** 여기서 partial 은 전국 과소계상이 아니라
        # '특정 시군구가 통째로 빠짐'이라 나머지 246개는 정상값이다. 대신 크게 알린다 —
        # 그 지역은 화면에서 '어린이집 0개'로 보이고, 0 은 실측처럼 생겼다.
        un = (cc["meta"].get("unresolved_sgg") or {})
        print(f"[metrics] ⚠️ childcare.json 부분수집 — 시군구 {len(un)}개가 비어 있다: "
              f"{', '.join(f'{k} {v}' for k, v in list(un.items())[:8])}")
    if cc and cc.get("data") and not (cc.get("meta") or {}).get("sample"):
        out["childcare"] = {
            "adm": {k: v.get("daycare_cnt", 0) for k, v in cc["data"].items()},
            "extra": {k: {"daycare_cnt": v.get("daycare_cnt"),
                          "daycare_kids": v.get("daycare_kids"),
                          "daycare_capacity": v.get("daycare_capacity"),
                          "daycare_age_unassigned": v.get("daycare_age_unassigned"),
                          **{f"daycare_age_{age}": v.get(f"daycare_age_{age}", 0)
                             for age in range(6)}}
                      for k, v in cc["data"].items()},
            "national": True,
            "source": "childcare.json",
            "base_ym": (cc.get("meta") or {}).get("base_ym"),
        }

    aca = read_json(PROCESSED / "academy.json")
    if aca and aca.get("data") and not (aca.get("meta") or {}).get("sample"):
        out["academy"] = {
            "adm": {k: v.get("academy_cnt", 0) for k, v in aca["data"].items()},
            "extra": {k: {"academy_capacity": v.get("capacity")}
                      for k, v in aca["data"].items()},
            "national": True,
            "source": "academy.json",
            "geocode_failed": (aca.get("meta") or {}).get("geocode_failed"),
        }
    return out


def load_poi() -> tuple[dict, dict, dict, list[str]]:
    """
    poi_<sgg>.json 전부를 읽어 행정동/시군구/시도 단위 개수로 집계.
    좌표는 여기서 쓰지 않는다(개수만). adm_cd 없는 POI 는 시군구 집계에만 반영.

    ⚠️ 전국 산출물(school.json / academy.json)이 있으면 그쪽이 우선이다.
       이 경로는 아직 전국화되지 않은 종류의 폴백으로만 남는다.
    """
    adm: dict[str, dict[str, int]] = {}
    sgg: dict[str, dict[str, int]] = {}
    sido: dict[str, dict[str, int]] = {}
    files = sorted(PROCESSED.glob("poi_*.json"))
    covered: list[str] = []
    for f in files:
        code = f.stem.replace("poi_", "")
        if not code.isdigit():
            continue
        d = read_json(f)
        if not d:
            continue
        covered.append(code)
        for kind in ("academy", "school"):
            for p in (d.get(kind) or []):
                a = (p or {}).get("adm_cd")
                if a:
                    adm.setdefault(a, {}).setdefault(kind, 0)
                    adm[a][kind] += 1
                sgg.setdefault(code, {}).setdefault(kind, 0)
                sgg[code][kind] += 1
                sido.setdefault(code[:2], {}).setdefault(kind, 0)
                sido[code[:2]][kind] += 1
        # POI 가 0건인 종류도 '측정됨(0)' 으로 남긴다
        for kind in ("academy", "school"):
            sgg.setdefault(code, {}).setdefault(kind, 0)
            sido.setdefault(code[:2], {}).setdefault(kind, 0)
    return adm, sgg, sido, covered


# ----------------------------------------------------------------- 조립

def div(a, b, nd: int = 3):
    """0/None 을 조용히 0 으로 만들지 않는 나눗셈. 분모가 없으면 결측(None)."""
    if a is None or not b:
        return None
    return round(a / b, nd)


# --------------------------------------------------------------------------
# 12개월 인구 증감 — 행정구역 개편 구간은 **값을 내지 않는다** [2026-08-13]
# --------------------------------------------------------------------------
# 증감률은 12개월 전과 지금을 나눈 값이라, 그 사이에 **코드가 신설되면 산수가 무의미**하다.
# 실제로 이렇게 나왔었다:
#   · 검단구 +49.2% · 영종구 +48.3% · 음성군 +30.1% — 전부 분구·읍 승격의 점프였다
#   · 아라1동은 앞 3개월이 0 이라 화면이 이미 «–» 로 뒀다 (동 단위만 막혀 있었다)
# 가맹상담 자료에서 "인구 49% 증가"는 그대로 설득 근거가 되므로 **지운다.**
#
# ⚠️ **급증을 다 지우면 안 된다.** 광명2동 +157%·잠실4동 +55%는 재건축 입주라 **진짜 신호**다.
#    그래서 임계값(±%)으로 자르지 않고 **코드 신설이라는 사실**로만 판정한다:
#      ① trend12 에 0 이 있다      = 그 달엔 그 코드가 없었다 (신설·분구)
#      ② 같은 시군구·같은 달에 ±10% 넘게 변한 동 = 분동의 반대편
#         (전주 송천3동 신설 ↔ 송천1동 −49.6%. 송천1동엔 0 이 없어 ①로는 안 잡힌다)
#      ③ ①②가 하나라도 있으면 그 시군구·시도 합계도 오염 — 같이 지운다
#    ①이 없는 시군구의 급변(광명·경주 황오동 등)은 **건드리지 않는다.**
TREND_JUMP = 0.10


def trend_breaks(pop_adm: dict) -> dict[str, str]:
    """행정구역 개편으로 12개월 비교가 불가능한 코드 → 사유. 행정동 원본으로만 판정한다."""
    data = pop_adm.get("data") or {}
    breaks: dict[str, str] = {}
    new_at: dict[str, set[int]] = {}          # 시군구 → 코드가 생긴 달의 인덱스
    for code, v in data.items():
        t = v.get("trend12") or []
        if not t or 0 not in t:
            continue
        breaks[code] = "코드 신설"
        new_at.setdefault(code[:5], set()).update(
            i for i in range(1, len(t)) if t[i - 1] == 0 and t[i])
    for code, v in data.items():                              # ② 분동 반대편
        if code in breaks or code[:5] not in new_at:
            continue
        t = v.get("trend12") or []
        for i in new_at[code[:5]]:
            if 0 < i < len(t) and t[i - 1] and abs(t[i] / t[i - 1] - 1) > TREND_JUMP:
                breaks[code] = "같은 달 분동·경계조정"
                break
    for code in list(breaks):                                 # ③ 상위 롤업
        breaks.setdefault(code[:5], "하위 행정동 개편")
        breaks.setdefault(code[:2], "하위 행정동 개편")
    return breaks


def assemble(level: str, pop: dict, apt: dict, poi_cnt: dict,
             poi_covered_sgg: set[str], names: dict[str, str],
             nat: dict | None = None, kapt: dict | None = None,
             breaks: dict[str, str] | None = None) -> tuple[dict, dict]:
    """한 레벨(adm/sgg/sido)의 지표 + 점수를 만든다."""
    nat = nat or {}
    breaks = breaks or {}
    kdata = (kapt or {}).get("data") or {}
    # 전국 산출물은 레벨별로 합산해 둔다 (adm 값을 상위로 롤업)
    nat_lv: dict[str, dict] = {}
    for kind, blk in nat.items():
        roll: dict[str, int] = {}
        extra: dict[str, dict] = {}
        for adm_cd, n in blk["adm"].items():
            key = adm_cd if level == "adm" else (
                adm_cd[:5] if level == "sgg" else adm_cd[:2])
            roll[key] = roll.get(key, 0) + (n or 0)
            for ek, ev in (blk.get("extra", {}).get(adm_cd) or {}).items():
                if ev is None:
                    continue
                extra.setdefault(key, {})
                extra[key][ek] = extra[key].get(ek, 0) + ev
        nat_lv[kind] = {"cnt": roll, "extra": extra}

    # 지역 유니버스: 인구 > 아파트 > POI 순으로 합집합
    codes = set(pop["data"]) | set(apt["data"]) | set(poi_cnt)
    # D-1 종결(2026-08-04) — 출장소는 **행정동 목록에서만** 뺀다.
    # 시군구·시도 집계는 원천(population_sgg/sido)이 이미 소속 시군구에 합산해 갖고
    # 있으므로 총인구 51,091,769명은 그대로 보존된다. 여기서 빼는 건 "지도에 못 그리는
    # 이름 없는 행"뿐이다. 시군구까지 같이 빼면 총인구 보존 가드가 깨진다.
    dropped_branch: list[str] = []
    if level == "adm":
        dropped_branch = sorted(codes & BRANCH_OFFICES)
        codes -= BRANCH_OFFICES
    codes = sorted(c for c in codes if c)

    rec: dict[str, dict] = {}
    for c in codes:
        p = pop["data"].get(c) or {}
        a = apt["data"].get(c) or {}
        q = poi_cnt.get(c)
        # POI 파일이 있는 시군구에 속하면 '0건'도 실측치. 아니면 미측정(null).
        measured = (c[:5] in poi_covered_sgg) if level == "adm" else (c in poi_covered_sgg)

        def poi(kind):
            # 전국 산출물이 있으면 그게 정본이다. 목록에 없는 지역은 '실측 0건'이지
            # '미측정'이 아니다 — 여기를 null 로 두면 그 동만 경쟁강도 축이 빠져
            # 상대적으로 높은 점수를 받는다.
            if kind in nat_lv:
                return nat_lv[kind]["cnt"].get(c, 0)
            if q and kind in q:
                return q[kind]
            return 0 if measured else None

        school_age = p.get("school_age")
        academy = poi("academy")
        school = poi("school")

        # ---- 소비력 ②③ + 보정 (C-5, 2026-08-05) --------------------------
        # ⚠️ `households` 는 **주민등록 세대수**(인구 원천)이고 `apt_households` 는
        #    **아파트 세대수**(K-apt)다. 예전엔 apartment.json 쪽 households(전건 null)를
        #    폴백으로 받아 두 뜻이 한 이름에 섞일 수 있었다 — 그 폴백을 없앴다.
        k = kdata.get(c) or {}
        reg_hh = p.get("households") or None            # 주민등록 세대수
        apt_hh = k.get("households") or None            # 아파트 세대수(K-apt)
        avg_price = a.get("avg_price")
        low_sample = a.get("low_sample")

        # ② 규모·밀도 — 아파트 거주 비율.
        #    ⚠️ **100% 를 넘는 동이 67곳 있다(2026-08-05 실측).** 값을 깎지 않고
        #       그대로 내보내되 `apt_ratio_suspect` 로 표시한다 — 깎으면 어디가
        #       이상한지 사라지고, 그대로 두면 상담 자리에서 "아파트 거주 188%"가 나온다.
        #
        #    원인은 **경계 폴리곤과 주민등록의 행정동이 서로 다른 지역**이다.
        #    예) 전주 만성지구 — 주민등록은 `혁신동`(2019 분동)인데 경계 폴리곤은
        #        그 땅을 아직 `여의동`으로 갖고 있다. 그래서 아파트 13,296세대가
        #        여의동(주민등록 7,053세대)에 쌓이고, 혁신동은 1,943세대로 비었다.
        #        지오코딩 캐시의 `xcheck='mismatch'` 가 같은 지점을 가리킨다
        #        (전국 1,300건 / 1.2%, 여의동 98 · 송천3동 113 · 구월1동 65 …).
        #    ⚠️ 이건 C-5 조인의 버그가 아니라 **경계 원천의 분동 반영 지연**이다.
        #       고치려면 POI 배정 규칙 자체를 바꿔야 해서(학교·학원까지 전부 영향) 별건이다 → E-4.
        apt_ratio = div(apt_hh, reg_hh)
        apt_ratio_suspect = bool(apt_ratio and apt_ratio > 1)

        # ③ 총량 — 주거자산 총액(억원) = 아파트 세대수 × 평균 매매가.
        #    ⚠️ `low_sample`(24개월 매매 10건 미만) 동은 평균가 자체가 대표성이 없다.
        #       세대수를 곱하면 그 오차가 수천억 단위로 증폭되므로 **결측**으로 둔다.
        housing_asset = (round(apt_hh * avg_price / 10000)
                         if (apt_hh and avg_price and not low_sample) else None)

        # 보정 — 임대 비중·준공 연차. 순위엔 안 쓰고 해석용이다.
        #   '혼합'(분양+임대 한 단지)은 구성비가 K-apt 에 없다 → 임대에 더하면 과대,
        #   버리면 과소다. 그래서 하한(rental_ratio)과 상한(rental_ratio_max)을 같이 낸다.
        rental_ratio = div(k.get("rental_hh"), apt_hh)
        rental_ratio_max = div((k.get("rental_hh") or 0) + (k.get("mixed_hh") or 0), apt_hh)
        built_age = div(k.get("age_wsum"), k.get("age_hh"), 1)

        # 신축 비중 — **세대 기준**으로 교정했다 (계약 §2-4, 2026-08-05).
        #   기존 값은 '거래' 기준이라 회전율 높은 신축이 부풀려졌다. 비교용으로
        #   `new_ratio_deal` 에 남긴다(같은 이름으로 덮으면 옛 값과 조용히 섞인다).
        new_ratio_hh = div(k.get("new_hh"), k.get("age_hh"))

        # 학원 0개는 '기회'일 수도 '수요 부재'일 수도 있어 순위를 매기지 않는다(null).
        # 대신 no_academy 플래그로 드러내고, 경쟁강도 가중치는 재정규화로 빠진다.
        ppa = None
        if school_age is not None and academy:
            ppa = round(school_age / academy, 1)

        rec[c] = {
            "name": names.get(c),
            # 시장규모
            "school_age": school_age,
            "preschool": (p.get("age") or {}).get("0_5"),
            # 행정구역 개편 구간은 값을 내지 않는다 (trend_breaks 주석 참조).
            # 사유를 같이 실어 화면이 «–» 옆에 이유를 띄운다 — 결측처럼 보이면 안 된다.
            "trend_pct": None if c in breaks else p.get("trend_pct"),
            "trend_break": breaks.get(c),
            "population": p.get("total"),
            # 경쟁강도
            "academy_cnt": academy,
            "pupils_per_academy": ppa,
            # 구매력(소비력) — 자산수준 정본은 **중앙 평당가**다 (2026-08-04).
            # avg_price_per_py 는 비교용으로만 남긴다. low_sample 인 동은 화면이
            # 숫자 대신 "표본 부족"을 띄운다 — 우연히 나온 가격이 순위에 섞이면 안 된다.
            "median_price_per_py": a.get("median_price_per_py"),
            "avg_price_per_py": a.get("avg_price_per_py"),
            "avg_price": a.get("avg_price"),
            "low_sample": a.get("low_sample"),
            "deal_count": a.get("deal_count"),
            "new_ratio": new_ratio_hh,                 # 세대 기준 (C-5 교정)
            "new_ratio_deal": a.get("new_ratio"),      # 거래 기준 (참고·구값)
            "households": p.get("households"),         # ⚠️ 주민등록 세대수
            "apt_households": apt_hh,                  # ⚠️ 아파트 세대수(K-apt)
            "apt_complexes": k.get("complexes") or None,
            "apt_ratio": apt_ratio,                    # ② 규모·밀도
            "apt_ratio_suspect": apt_ratio_suspect,    # 100% 초과 = 경계·배정 확인 필요
            "housing_asset": housing_asset,            # ③ 총량 (억원)
            "rental_ratio": rental_ratio,              # 보정 (하한)
            "rental_ratio_max": rental_ratio_max,      # 보정 (상한·'혼합' 포함)
            "built_age": built_age,                    # 보정 (세대 가중 평균 준공연차)
            # 접근성
            "school_cnt": school,
            # 학원이 실측 0개인 지역 (점수에는 미반영, 별도 검토 대상)
            "no_academy": (academy == 0) if academy is not None else None,
        }
        # 참고 필드 — 점수 산식에는 넣지 않는다.
        # 학생수를 access_school 산식에 반영하는 것은 구조 변경이라 A-8 승인 대상.
        for kind in nat_lv:
            for ek, ev in (nat_lv[kind]["extra"].get(c) or {}).items():
                rec[c][ek] = ev

        # ---- 유아 연령 합산 (2026-08-12) ------------------------------------
        # `students_age_N` 은 원래 **학교 재적 + 유치원 원아**를 한 필드에 더해 온 축이다
        # (load_school.py 집계 참조). 어린이집도 같은 축에 더한다.
        #   · 만3~5세 — 유치원(교육부)이 인구의 46~58% 만 잡는다. 나머지가 어린이집이고
        #     **두 원천은 배타적이라 합산해도 중복이 아니다**(STATUS 2026-08-11 실측).
        #   · 만0~2세 — 유치원에 아예 없던 구간이라 어린이집이 유일한 원천이다.
        #     화면 한국나이 3~4세 칸이 여기서 채워진다(만 나이 축, toManAge).
        # 혼합·특수반(daycare_age_unassigned)은 나이를 특정할 수 없어 더하지 않는다.
        for n in range(6):
            dv = rec[c].get(f"daycare_age_{n}")
            if dv:
                rec[c][f"students_age_{n}"] = (rec[c].get(f"students_age_{n}") or 0) + dv
        # 유아 시설·원아 합계(유치원+어린이집). 화면 라벨이 「유치원·어린이집」인 칸의 값.
        kc, dc = rec[c].get("kinder_cnt"), rec[c].get("daycare_cnt")
        kk, dk = rec[c].get("kinder_kids"), rec[c].get("daycare_kids")
        if kc is not None or dc is not None:
            rec[c]["preschool_cnt"] = (kc or 0) + (dc or 0)
            rec[c]["preschool_kids"] = (kk or 0) + (dk or 0)

    # 🚫 점수·등급·백분위 랭킹 산출 없음 (2026-07-31 폐기, 상단 주석 참조)
    #    지표는 실측값 그대로 내보내고, 비교·정렬은 화면이 선택한 지표로 한다.

    stat = {
        "count": len(codes),
        "with_population": sum(1 for c in codes if rec[c]["school_age"] is not None),
        "with_apartment": sum(1 for c in codes if rec[c]["avg_price_per_py"] is not None),
        "with_academy": sum(1 for c in codes if rec[c]["academy_cnt"] is not None),
        "with_school": sum(1 for c in codes if rec[c]["school_cnt"] is not None),
        "no_academy": sum(1 for c in codes if rec[c].get("no_academy")),
        "low_sample_price": sum(1 for c in codes if rec[c].get("low_sample")),
        # C-5 — 아파트 세대수가 붙은 지역 수. 여기가 갑자기 줄면 K-apt 조인이 깨진 것이다
        # 어린이집 — 여기가 0 이면 childcare.json 이 안 붙은 것이다(운영키·수집 확인)
        "with_daycare": sum(1 for c in codes if rec[c].get("daycare_cnt")),
        "daycare_kids_total": sum(rec[c].get("daycare_kids") or 0 for c in codes),
        "with_apt_households": sum(1 for c in codes if rec[c].get("apt_households")),
        "apt_households_total": sum(rec[c].get("apt_households") or 0 for c in codes),
        # 아파트 세대 > 주민등록 세대인 지역 (hoCnt 대체분·미입주 등) — 자르지 않고 드러낸다
        "apt_ratio_over_1": sum(1 for c in codes if (rec[c].get("apt_ratio") or 0) > 1),
        "dropped_branch_office": dropped_branch,      # D-1
    }
    # 인구에 없는 코드(= 다른 팀원 산출물 간 코드 불일치)를 드러낸다
    orphan = [c for c in codes if c not in pop["data"] and pop["data"]]
    stat["orphan_codes"] = orphan[:50]
    stat["orphan_count"] = len(orphan)
    return rec, stat


# ----------------------------------------------------------------- B-11 하드 가드

# D-1 은 2026-08-04 종결됐다 — 출장소 3건은 assemble() 에서 유니버스 자체에서 빠지므로
# 여기 도달하지 않는다. 예외 목록은 **비운 채로 남겨 둔다**: 다시 채워지는 일이 생기면
# 그건 "예외를 늘려야 할 상황"이라는 신호이고, 가드에 걸려 멈추는 게 맞다.
KNOWN_ORPHANS: set[str] = set()

LEGACY_SIDO_PREFIX = ("29", "46")     # 2026-07-01 폐지된 광주·전남


def hard_guard(metrics: dict[str, dict], strict: bool = True) -> list[str]:
    """재생성 결과를 종료 전에 검사한다 (TODO: B-11).

    B-1(2026-07-29) 은 사람이 눈으로 확인했을 뿐 강제되지 않았다. 입력 하나만 낡아도
    metrics 가 **조용히** 오염되므로(orphan 416건 사고) 여기서 죽는 편이 낫다.
    """
    errs: list[str] = []

    # ① metrics 키 ⊆ 경계
    geo_codes = set()
    for f in (ROOT / "data" / "geo").glob("adm_*.geojson"):
        try:
            gj = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ft in gj.get("features", []):
            code = (ft.get("properties") or {}).get("code")
            if code:
                geo_codes.add(code)
    if geo_codes:
        orphan = set(metrics.get("adm", {})) - geo_codes - KNOWN_ORPHANS
        if orphan:
            errs.append(f"① 경계에 없는 행정동 {len(orphan)}건: {sorted(orphan)[:10]}")

    # ② 폐지된 구코드 잔존
    for lv, d in metrics.items():
        bad = [c for c in d if c.startswith(LEGACY_SIDO_PREFIX)]
        if bad:
            errs.append(f"② {lv} 에 폐지 구코드(29/46) {len(bad)}건 잔존: {bad[:10]}")

    # ③ 시도 16건 + 통합코드 12 포함
    sido = set(metrics.get("sido", {}))
    if sido:
        if len(sido) != 16:
            errs.append(f"③ 시도가 16개가 아니다 ({len(sido)}개): {sorted(sido)}")
        if "12" not in sido:
            errs.append("③ 전남광주통합특별시(12) 가 없다 — 호남권이 지도에서 백지가 된다")

    if errs and strict:
        raise RuntimeError("[B-11 하드가드] 재생성 결과가 계약을 위반했다:\n  - "
                           + "\n  - ".join(errs))
    return errs


def name_map() -> dict[str, str]:
    cm = read_json(PROCESSED / "code_map.json") or {}
    out = {}
    for key in ("sido", "sgg", "adm"):
        for x in (cm.get(key) or []):
            if isinstance(x, dict) and x.get("code"):
                out[x["code"]] = x.get("name")
    return out


def main(argv=None) -> int:
    global QUIET
    ap = argparse.ArgumentParser(description="종합 지표 조립 (팀원3)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-guard", action="store_true",
                    help="B-11 하드가드를 경고로만 (기본은 위반 시 중단)")
    a = ap.parse_args(argv)
    QUIET = a.quiet

    names = name_map()
    nat = load_national()
    for kind, blk in nat.items():
        log(f"전국 산출물 {kind}: {blk['source']} · 행정동 {len(blk['adm']):,}개 "
            f"→ 모집단 전국화(B-4)")
    poi_adm, poi_sgg, poi_sido, covered = load_poi()
    covered_set = set(covered)
    log(f"POI 파일 {len(covered)}개 시군구 ({', '.join(covered[:8])}{'...' if len(covered) > 8 else ''})"
        if covered else "! POI 파일 없음 → 경쟁강도·접근성 지표는 null")

    missing: list[str] = []
    sample_flags: list[str] = []
    built: dict[str, dict] = {}
    out_files = {"adm": "metrics.json", "sgg": "metrics_sgg.json",
                 "sido": "metrics_sido.json"}

    # 개편 판정은 **행정동 원본 하나로만** 한다 — 상위 레벨 시계열은 개편이 이미 섞여 들어와
    # 있어서 그것만 보면 "인구가 늘었다"와 구분이 안 된다.
    breaks = trend_breaks(load_population("adm"))
    if breaks:
        lv = {8: "동", 5: "시군구", 2: "시도"}
        cnt = {n: sum(1 for c in breaks if len(c) == k) for k, n in lv.items()}
        log(f"[12개월 증감] 행정구역 개편 구간 제외 — "
            + " · ".join(f"{n} {v}개" for n, v in cnt.items() if v))

    for level in ("adm", "sgg", "sido"):
        pop = load_population(level)
        apt = load_apartment(level)
        kapt = load_kapt(level)                       # C-5 아파트 세대수
        if not pop["data"]:
            missing.append(pop["file"])
        if not apt["data"]:
            missing.append(apt["file"])
        if pop["meta"].get("sample"):
            sample_flags.append(pop["file"])
        if apt["meta"].get("sample"):
            sample_flags.append(apt["file"])

        poi_cnt = {"adm": poi_adm, "sgg": poi_sgg, "sido": poi_sido}[level]
        if level == "sido":
            # 시도는 산하 시군구가 **전부** 수집됐을 때만 실측으로 인정한다.
            # (일부만 있으면 서울 학원수가 강남+서초 합계가 되어버림)
            all_sgg = {k for k in (load_population("sgg")["data"] or {})} | set(poi_sgg)
            by_sido: dict[str, set[str]] = {}
            for s in all_sgg:
                by_sido.setdefault(s[:2], set()).add(s)
            cov = {sd for sd, ss in by_sido.items() if ss and ss <= covered_set}
            # poi_sido 도 불완전 시도는 버린다
            poi_cnt = {k: v for k, v in poi_sido.items() if k in cov}
        else:
            cov = covered_set
        rec, stat = assemble(level, pop, apt, poi_cnt, cov, names, nat, kapt, breaks)
        built[level] = rec

        meta = {
            "sample": False,
            "updated": date.today().isoformat(),
            "level": level,
            "scoring": "retired",
            "scoring_note": ("종합점수·등급은 2026-07-31 폐기됐다(관리자 결정). "
                             "이 파일은 실측 지표만 담는다. 재설계는 docs/TODO.md F-1, "
                             "폐기된 산식 전문은 docs/SCORE.md 에 보존."),
            # 화면이 이 값을 보고 유아 칸을 「유치원」/「유치원·어린이집」으로 바꾼다.
            # 여기가 false 면 만0~2세는 원천이 없는 것이므로 '데이터 수집중'으로 띄운다.
            "axes": {"childcare": "childcare" in nat},
            "inputs": {
                "population": pop["file"] if pop["data"] else None,
                "apartment": apt["file"] if apt["data"] else None,
                "kapt": kapt["file"] if kapt["data"] else None,
                "poi_sgg_covered": len(covered),
            },
            "stat": stat,
        }
        if sample_flags:
            meta["sample"] = True
            meta["sample_inputs"] = sorted(set(sample_flags))
        write_json(PROCESSED / out_files[level], {"meta": meta, "data": rec})
        log(f"  {level}: {stat}")

    if missing:
        log(f"! 아직 없는 입력 {sorted(set(missing))} → 해당 지표는 null. "
            f"파일이 생기면 build_metrics.py 를 다시 돌리면 채워집니다.")

    # ---- B-11 하드가드 (항상 마지막) ----
    errs = hard_guard(built, strict=not a.no_guard)
    if errs:
        log("! [B-11] 가드 위반(경고 모드): " + " / ".join(errs))
    else:
        log("[B-11] 하드가드 통과 — 키⊆경계 · 구코드 0 · 시도 16+12")

    if sample_flags:
        log(f"! 아직 sample 인 입력: {sorted(set(sample_flags))} "
            f"→ 대외 사용 금지 (발행 게이트 C-8 에서 전수 확인)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
