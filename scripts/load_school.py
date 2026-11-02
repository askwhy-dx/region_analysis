# -*- coding: utf-8 -*-
"""
load_school.py — 학교 데이터 로더 (TODO: C-1)

학교알리미(schoolinfo.go.kr) / 유치원알리미 CSV 를 읽어 행정동 단위로 집계한다.
출처·컬럼·함정은 docs/DATA_SOURCES.md §5 가 SSOT.

원천 (관리자 제공 파일 · **연 1회 공시 갱신**)
  학교기본정보(초/중/고)_전체.csv              좌표·폐교/휴교/분교 플래그
  2026년도_학년별·학급별 학생수(초/중/고)_전체.csv   학년별 학생수·학급수
  2026년도_전국유치원현황.csv                   원아수·좌표 (현재 점수 산식 미사용)
  → 다운로드: https://www.schoolinfo.go.kr/ng/go/pnnggo_a01_l2.do
              https://e-childschoolinfo.moe.go.kr/openData.do

산출
  data/processed/school.json
    meta : sample=false · base_year · source_file 목록 · sha256 · 집계 리포트
    data : {adm_cd: {school_cnt, elem_cnt, mid_cnt, high_cnt, students, ...}}
    poi  : {sgg_cd: [ {name, type, lat, lng, adm_cd, students, grades, grade_classes} ]}

사용법
  python scripts/load_school.py                 # 기본 경로에서 읽어 산출
  python scripts/load_school.py --src <폴더>
  python scripts/load_school.py --selftest      # 표본 검증만

⚠️ 규칙 (docs/DATA_SOURCES.md §5)
  * `법정동코드` 를 행정동으로 쓰지 않는다 (법정동:행정동 N:M).
    시군구 힌트로만 쓰고 **최종 판정은 위도·경도 PIP** 가 한다.
  * 기본정보 행수 > 학생수 행수다. 폐교·휴교를 먼저 거른 뒤 학교코드로 조인하고,
    **탈락 건수를 전부 로그로 남긴다.** 조용히 버리면 "학교 없는 동"으로 오판된다.
  * 접근성 축(access_school 0.15)은 **초·중만** 집계한다 (docs/SCORE.md §3).
    고등학교는 보관만 하고 축에 넣지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admlookup import AdmIndex, LegacyMap          # noqa: E402

# Windows 콘솔은 cp949 라 ⚠ 같은 기호에서 UnicodeEncodeError 로 죽는다.
# 한글은 그대로 두고 표현 불가 문자만 치환한다 (utf-8 로 바꾸면 콘솔에서 한글이 깨진다).
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

def load_coord_fix(path):
    """{학교코드: {lat, lng, ...}} — 없으면 빈 dict (보정은 선택 사항이다)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "school.json"
DEFAULT_SRC = Path(r"C:\Users\User\Desktop\test\지역회원\학교학생수")
MANUAL_DIR = ROOT / "data" / "manual" / "school"
# 원천 CSV 의 위경도가 **동명이교와 겹친** 학교의 수기 보정 (2026-09-18).
#   학교알리미 CSV 는 같은 이름의 다른 학교에 같은 좌표를 주는 자리가 있다 —
#   구리 인창중·인창고가 서울 인창중·인창고 좌표에 얹혀 서대문구 충현동에 찍혔다.
#   ⚠️ 좌표만 바꾼다. 행정동은 바뀐 좌표로 **다시 PIP** 하므로 여기에 적지 않는다.
COORD_FIX = ROOT / "data" / "manual" / "school_coord_fix.json"

LEVELS = {"초": "elem", "중": "mid", "고": "high"}
GRADES = {"초": 6, "중": 3, "고": 3}
# 학령인구 연령 필터와 학교 학년을 연결하는 기준.
# 주민등록 인구의 만 나이와 학교 학년은 개인별로 다를 수 있으므로, 화면에서는
# 공시 학년 학생수를 아래의 표준 취학 연령에 대응해 집계한다.
GRADE_AGES = {
    "초": tuple(range(6, 12)),
    "중": tuple(range(12, 15)),
    "고": tuple(range(15, 18)),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def read_csv(p: Path) -> list[dict]:
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def num(v):
    v = (v or "").replace(",", "").strip()
    if not v:
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


def find_src(src: Path, level: str) -> tuple[Path, Path]:
    base = src / f"학교기본정보({level})_전체.csv"
    cand = sorted(src.glob(f"*학년별*학생수({level})_전체.csv"))
    if not base.exists():
        raise FileNotFoundError(base)
    if not cand:
        raise FileNotFoundError(f"{src}/*학년별*학생수({level})_전체.csv")
    return base, cand[0]


def base_year_of(p: Path) -> int:
    """파일명 앞의 '2026년도' 에서 기준연도를 뽑는다. 없으면 올해."""
    import re
    m = re.search(r"(20\d{2})년도", p.name)
    return int(m.group(1)) if m else date.today().year


# --------------------------------------------------------------------------

def load_level(src: Path, level: str, idx: AdmIndex, lg: LegacyMap, rep: dict):
    """한 학교급 처리 → (배치된 학교 리스트, 기준연도)"""
    fb, fs = find_src(src, level)
    basic = read_csv(fb)
    stud = read_csv(fs)
    r = rep[level] = {
        "file_basic": fb.name, "file_students": fs.name,
        "sha256_basic": sha256(fb), "sha256_students": sha256(fs),
        "rows_basic": len(basic), "rows_students": len(stud),
    }

    # 1) 폐교·휴교 제외  ← 조용히 버리지 않고 센다
    alive, closed, suspended = [], 0, 0
    for row in basic:
        if (row.get("폐교여부") or "").strip().upper() == "Y":
            closed += 1
            continue
        if (row.get("휴교여부") or "").strip().upper() == "Y":
            suspended += 1
            continue
        alive.append(row)
    r["dropped_closed"] = closed
    r["dropped_suspended"] = suspended

    # 2) 학생수 인덱스 (제외여부 Y 는 공시 제외분)
    smap, excluded = {}, 0
    for row in stud:
        if (row.get("제외여부") or "").strip().upper() == "Y":
            excluded += 1
            continue
        smap[(row.get("정보공시 학교코드") or "").strip()] = row
    r["students_excluded"] = excluded

    # 3) 학교코드로 조인 (명칭 조인 금지)
    #    분교는 학생수가 본교에 합산되는 경우가 많아 자기 행이 없다 → 조인에서 자연 탈락.
    #    학교 수를 부풀리지 않도록 그대로 두되, 몇 건인지 남긴다.
    out, no_student, no_coord, no_adm = [], 0, 0, 0
    coord_fix, fixed = load_coord_fix(COORD_FIX), []
    branch_dropped = 0
    matched_codes = set()
    for row in alive:
        code = (row.get("정보공시 학교코드") or "").strip()
        s = smap.get(code)
        if not s:
            no_student += 1
            if (row.get("분교여부") or "").strip().upper() == "Y":
                branch_dropped += 1
            continue
        matched_codes.add(code)
        try:
            lat = float((row.get("위도") or "").strip())
            lng = float((row.get("경도") or "").strip())
        except ValueError:
            no_coord += 1
            continue
        if not (124.0 <= lng <= 132.0 and 33.0 <= lat <= 39.0):
            no_coord += 1
            continue

        fx = coord_fix.get(code)
        if fx:
            lat, lng = float(fx["lat"]), float(fx["lng"])
            fixed.append(code)

        bjd = (row.get("법정동코드") or "").strip()
        hint = lg.fix_sgg(bjd[:5]) if len(bjd) >= 5 else None
        adm = idx.lookup(lng, lat, sgg_hint=hint)
        if not adm:
            no_adm += 1
            continue

        total = num(s.get("학생수(계)"))
        by_grade = [num(s.get(f"{g}학년 학생수")) for g in range(1, GRADES[level] + 1)]
        age_unassigned = max(0, total - sum(by_grade))
        out.append({
            "code": code,
            "name": (row.get("학교명") or "").strip(),
            "type": level,
            "lat": round(lat, 6), "lng": round(lng, 6),
            "adm_cd": adm,
            "students": total,
            "grades": by_grade,
            "grade_classes": [num(s.get(f"{g}학년 학급수")) for g in range(1, GRADES[level] + 1)],
            "age_unassigned": age_unassigned,
            "classes": num(s.get("학급수(계)")),
            "branch": (row.get("분교여부") or "").strip().upper() == "Y",
            "founder": (row.get("설립구분") or "").strip(),
            # E-4 재판정용. 폴리곤이 낡은 동에 떨어진 학교만 이 주소로 다시 묻는다
            "addr": (row.get("학교도로명 주소") or "").strip(),
        })

    if fixed:
        r["coord_fixed"] = fixed          # 수기 보정이 몇 건 먹었는지 산출물에 남긴다
        print(f"  [보정] 좌표 수기 보정 {len(fixed)}건 — {', '.join(fixed)}")
    r["dropped_no_student_row"] = no_student
    r["dropped_branch_no_row"] = branch_dropped
    r["dropped_no_coord"] = no_coord
    r["dropped_no_adm_match"] = no_adm
    r["placed"] = len(out)
    # 학생수 파일에만 있고 기본정보에 없는 학교 = 좌표가 없어 배치 불가
    r["students_only_no_basic"] = len(set(smap) - matched_codes)
    r["base_year"] = base_year_of(fs)
    return out, r["base_year"]


def load_kindergarten(src: Path, idx: AdmIndex, rep: dict):
    """유치원 — 현재 점수 산식에는 넣지 않는다. 유아 확장 대비 집계만 보관."""
    cand = sorted(src.glob("*전국유치원현황.csv"))
    if not cand:
        rep["유치원"] = {"skipped": "파일 없음"}
        return []
    f = cand[0]
    rows = read_csv(f)
    out, no_coord, no_adm, age_unassigned = [], 0, 0, 0
    for row in rows:
        try:
            lat = float((row.get("위도") or "").strip())
            lng = float((row.get("경도") or "").strip())
        except ValueError:
            no_coord += 1
            continue
        if not (124.0 <= lng <= 132.0 and 33.0 <= lat <= 39.0):
            no_coord += 1
            continue
        adm = idx.lookup(lng, lat)
        if not adm:
            no_adm += 1
            continue
        age_students = {age: num(row.get(f"만{age}세원아수")) for age in range(3, 6)}
        unassigned = sum(num(row.get(k)) for k in ("혼합원아수", "특수원아수"))
        age_unassigned += unassigned
        kids = sum(age_students.values()) + unassigned
        out.append({"name": (row.get("유치원명") or "").strip(), "type": "유치원",
                    "lat": round(lat, 6), "lng": round(lng, 6),
                    "adm_cd": adm, "students": kids,
                    "age_students": age_students, "age_unassigned": unassigned,
                    "founder": (row.get("설립유형") or "").strip(),
                    "addr": (row.get("주소") or "").strip()})
    rep["유치원"] = {"file": f.name, "sha256": sha256(f), "rows": len(rows),
                    "dropped_no_coord": no_coord, "dropped_no_adm_match": no_adm,
                    "placed": len(out), "base_year": base_year_of(f),
                    "age_unassigned": age_unassigned}
    return out


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="학교 데이터 로더 (C-1)")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="CSV 폴더")
    ap.add_argument("--no-keep", action="store_true", help="원본을 data/manual 로 복사하지 않는다")
    ap.add_argument("--no-arbitrate", action="store_true",
                    help="E-4 재판정(JUSO) 생략 — 오프라인이거나 옛 기준 재현이 필요할 때")
    a = ap.parse_args(argv)

    src = Path(a.src)
    if not src.exists():
        print(f"[FATAL] 원본 폴더가 없다: {src}", file=sys.stderr)
        return 2

    print(f"[school] 원천: {src}")
    idx = AdmIndex.load(verbose=True)
    lg = LegacyMap.load()

    rep: dict = {}
    schools, years = [], set()
    for lvl in ("초", "중", "고"):
        got, by = load_level(src, lvl, idx, lg, rep)
        schools += got
        years.add(by)
        r = rep[lvl]
        print(f"  [{lvl}] 기본 {r['rows_basic']:,} → 배치 {r['placed']:,} "
              f"(폐교 {r['dropped_closed']} · 휴교 {r['dropped_suspended']} · "
              f"학생수행없음 {r['dropped_no_student_row']}[분교 {r['dropped_branch_no_row']}] · "
              f"좌표없음 {r['dropped_no_coord']} · 경계밖 {r['dropped_no_adm_match']})")
        if r["students_only_no_basic"]:
            print(f"       ⚠️ 학생수 파일에만 있는 학교 {r['students_only_no_basic']}건 "
                  f"— 기본정보에 없어 좌표가 없다(배치 불가)")

    kinder = load_kindergarten(src, idx, rep)
    if kinder:
        rk = rep["유치원"]
        print(f"  [유치원] {rk['rows']:,} → 배치 {rk['placed']:,} "
              f"(좌표없음 {rk['dropped_no_coord']} · 경계밖 {rk['dropped_no_adm_match']}) "
              f"— 점수 산식 미사용")

    # ---- E-4 재판정 : 낡은 폴리곤 동에 떨어진 건만 JUSO 로 다시 묻는다 ----
    #
    # 학교는 원천 CSV 의 좌표를 그대로 PIP 하므로 xcheck 가 없다 — 스스로 분쟁을
    # 알아채지 못한다. 그런데 **폴리곤은 주소 기반 POI 와 똑같은 것을 쓴다.**
    # 최근 분동된 지역(예: 전주 만성지구 — 주민등록은 혁신동, 폴리곤은 아직 여의동)
    # 에서는 학교도 같이 옆 동으로 쌓인다.
    #
    # 전량을 JUSO 에 묻는 건 낭비라, `arbitrate_adm.py` 가 남긴 **교정 이력**에서
    # 낡은 것으로 확인된 동만 골라 그 안의 학교만 재판정한다.
    # ⚠️ 그래서 이 보정은 **완전하지 않다.** 증거가 남지 않은 동(그 동에 주소 기반
    #    POI 가 아예 없던 곳)은 여전히 옛 폴리곤대로 배치된다.
    if not a.no_arbitrate:
        try:
            from adm_arbiter import Juso, NameIndex, stale_zones   # noqa: E402
            zones = stale_zones()
        except Exception as e:
            print(f"  ⚠️ E-4 재판정 건너뜀 — {e}")
            zones = set()
        if zones:
            idx_nm = NameIndex()
            j = Juso()
            cand = [s for s in schools + kinder
                    if s.get("adm_cd") in zones and s.get("addr")]
            print(f"  [E-4] 낡은 폴리곤 동 {len(zones)}개 · 재판정 대상 {len(cand):,}건")
            moved = failed = 0
            for s in cand:
                hemd = j.hemd(s["addr"])
                if not hemd:
                    failed += 1
                    continue
                cd, _ = idx_nm.resolve(hemd, s["adm_cd"])
                # 우리 경계에 없는 동은 채택하지 않는다 (붙일 폴리곤이 없다)
                if not cd or cd not in idx_nm.universe or cd == s["adm_cd"]:
                    continue
                s["adm_cd_pip"] = s["adm_cd"]
                s["adm_cd"] = cd
                s["arbiter"] = "juso"
                moved += 1
            j.save()
            rep["arbiter_e4"] = {"zones": len(zones), "candidates": len(cand),
                                 "moved": moved, "juso_failed": failed}
            print(f"  [E-4] 교정 {moved:,}건 · 조회실패 {failed:,}")

    # ---- 행정동 집계 ----
    agg = defaultdict(lambda: {
        "school_cnt": 0, "elem_cnt": 0, "mid_cnt": 0, "high_cnt": 0,
        "students": 0, "students_elem": 0, "students_mid": 0,
        "students_high": 0, "classes": 0,
        "kinder_cnt": 0, "kinder_kids": 0, "students_age_unassigned": 0,
        **{f"students_age_{age}": 0 for age in range(3, 18)},
    })
    for s in schools:
        d = agg[s["adm_cd"]]
        key = LEVELS[s["type"]]
        d[f"{key}_cnt"] += 1
        d[f"students_{key}"] += s["students"]
        d["classes"] += s["classes"]
        for age, students in zip(GRADE_AGES[s["type"]], s["grades"]):
            d[f"students_age_{age}"] += students
        d["students_age_unassigned"] += s["age_unassigned"]
        # ⚠️ access_school(0.15)은 초·중만 (SCORE.md §3). 고교는 별도 필드로만 남긴다.
        if s["type"] in ("초", "중"):
            d["school_cnt"] += 1
            d["students"] += s["students"]
    for k in kinder:
        d = agg[k["adm_cd"]]
        d["kinder_cnt"] += 1
        d["kinder_kids"] += k["students"]
        for age, students in k["age_students"].items():
            d[f"students_age_{age}"] += students
        d["students_age_unassigned"] += k["age_unassigned"]

    # ---- POI (시군구 분할) ----
    poi = defaultdict(list)
    for s in schools:
        poi[s["adm_cd"][:5]].append({
            "name": s["name"], "type": s["type"],
            "lat": s["lat"], "lng": s["lng"],
            "adm_cd": s["adm_cd"], "students": s["students"],
            # 지도 말풍선의 학년별 인원(2026-09-17). 특수·순회학급은 관리자 결정으로 표시 제외 —
            # 그래서 grades 합이 students 보다 작을 수 있다(차이 = age_unassigned).
            "grades": s["grades"],
            "grade_classes": s["grade_classes"],       # 말풍선 「1학년 186명 (7학급)」
        })

    # ---- 원본 보존 (파일이 SSOT 이므로) ----
    kept = []
    if not a.no_keep:
        MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        for p in sorted(src.glob("*.csv")):
            dst = MANUAL_DIR / p.name
            if not dst.exists() or dst.read_bytes() != p.read_bytes():
                dst.write_bytes(p.read_bytes())
            kept.append(p.name)

    base_year = max(years) if years else date.today().year
    obj = {
        "meta": {
            "sample": False,
            "updated": date.today().isoformat(),
            "source": "학교알리미(schoolinfo.go.kr) + 유치원알리미 CSV",
            "source_url": {
                "초중고": "https://www.schoolinfo.go.kr/ng/go/pnnggo_a01_l2.do",
                "유치원": "https://e-childschoolinfo.moe.go.kr/openData.do",
            },
            "base_year": base_year,
            "source_dir": str(src),
            "source_file": kept,
            "note": "access_school(0.15)은 초·중만 집계. 고교·유치원은 보관 필드.",
            "student_age_basis": {
                "3_5": "유치원알리미 만3·4·5세 원아수",
                "6_11": "초등학교 1~6학년 학생수의 표준 취학 연령 대응",
                "12_14": "중학교 1~3학년 학생수의 표준 취학 연령 대응",
                "15_17": "고등학교 1~3학년 학생수의 표준 취학 연령 대응",
                "unassigned": "유치원 혼합·특수 원아수는 연령을 확인할 수 없어 연령 필터 집계에서 제외",
            },
            "report": rep,
        },
        "data": {k: v for k, v in sorted(agg.items())},
        "poi": {k: v for k, v in sorted(poi.items())},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    n_elem = sum(v["elem_cnt"] for v in agg.values())
    n_mid = sum(v["mid_cnt"] for v in agg.values())
    n_high = sum(v["high_cnt"] for v in agg.values())
    print(f"\n[school] 배치 총계  초 {n_elem:,} · 중 {n_mid:,} · 고 {n_high:,} "
          f"· 유치원 {len(kinder):,}")
    print(f"[school] 행정동 커버리지 {len(agg):,}동 · 시군구 {len(poi)}개")
    print(f"[school] 초·중 학생수 합계 {sum(v['students'] for v in agg.values()):,}명 "
          f"(기준연도 {base_year})")
    print(f"[school] → {OUT}")
    if not a.no_keep:
        print(f"[school] 원본 {len(kept)}개 보존 → {MANUAL_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
