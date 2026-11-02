"""collect_childcare.py — 어린이집 수집 (정보공개포털 보육정보공개 API)

    python scripts/collect_childcare.py --fixture     운영키 없이 파이프라인만 점검
    python scripts/collect_childcare.py               전국 수집 (운영키 필요)
    python scripts/collect_childcare.py --sgg 26350,11680
    python scripts/collect_childcare.py --stat        수집 없이 현황만

무엇을 만드나
    data/raw/childcare/<YYYYMM>/<arcode>.xml   원본 보존
    data/processed/childcare.json              행정동 집계 (school.json 과 같은 모양)

왜 필요한가
    유치원(교육부)에는 **만0~2세가 아예 없고, 만3~5세도 절반뿐**이다.
    전국 실측(2026-08-11): 유치원 원아 / 주민등록 인구 = 만3세 45.9% · 만4세 54.3% · 만5세 57.6%.
    나머지가 어린이집이다. 두 원천은 배타적이라 **합산해도 중복이 아니다.**

[중요] **개발키로는 수집이 안 된다.**
    개발키는 arcode 를 무시하고 어느 시군구를 넣어도 같은 더미 1건(1,937B)을 준다.
    없는 코드(99999)까지 같은 응답이라 **지역 구분 자체가 없다** (실측 2026-08-11).
    값도 전부 01, 02, 03 … 순번이다. HTTP 200 이라 성공처럼 보이는 게 함정이다.
    → 그래서 이 스크립트는 더미를 감지하면 **processed 를 절대 쓰지 않고 멈춘다.**
      구조 점검만 하려면 --fixture 를 쓸 것.

원천: https://info.childcare.go.kr (보육정보공개 API)
  GET http://api.childcare.go.kr/mediate/rest/cpmsapi030/cpmsapi030/request
      ?key=<운영키>&arcode=<시군구코드 5자리>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admlookup import AdmIndex, LegacyMap  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "childcare"
PROCESSED = ROOT / "data" / "processed"
API = "http://api.childcare.go.kr/mediate/rest/cpmsapi030/cpmsapi030/request"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# CHILD_CNT_00~05 = 만0~5세 현원. 대시보드 students_age_N 과 같은 **만 나이** 축이다.
# (화면은 한국나이로 보여주지만 데이터는 만 나이 — web/index.html 의 toManAge 참고)
AGE_FIELDS = {f"CHILD_CNT_{n:02d}": n for n in range(6)}
# 혼합·특수반은 나이를 특정할 수 없다. 유치원의 혼합·특수 원아와 같은 취급으로 unassigned 에 넣는다.
MIX_FIELDS = ["CHILD_CNT_M2", "CHILD_CNT_M3", "CHILD_CNT_M5", "CHILD_CNT_SP"]
# 운영하지 않는 곳은 뺀다. 0 은 결측이 아니라 실측이므로, 빼는 기준은 **상태값**으로만 잡는다.
CLOSED_STATUS = ("폐지", "휴지")

# ─── 포털 arcode 오버라이드 (2026-08-12 실측) ────────────────────────────────
# **포털은 행안부 표준코드를 쓰지 않는 시군구가 있다.** 구 설치·시 승격 이전의 옛 코드나
# 자체 연번을 그대로 쓴다. 표준코드로 물으면 HTTP 200 + `INFO-200 검색결과가 없습니다` 가
# 와서 **"그 지역엔 어린이집이 0개"처럼 보인다** — 조용한 실패의 전형이다.
#
#   행안부(code_map)          포털 arcode
#   41463 용인시기흥구   →    41462      (포털은 처인41461·기흥41462·수지41463 연번)
#   44133 천안시서북구   →    44132      (포털은 동남44131·서북44132 · 44130 은 시 전체)
#
# ⚠️ 좌표 PIP 가 배정을 하므로 **어느 코드로 받아오든 동 배정은 맞는다.** 문제는 "못 받아온
#    지역"뿐이다. 그래서 오버라이드는 **누락된 코드만** 넣는다 — 이미 응답이 오는 코드를
#    중복으로 넣으면 같은 시설을 두 번 집계한다.
# ⚠️ 41465(수지)·44133 자리에 1건씩 남은 값은 경계 넘어온 인접 시설이다. **0 이 아니라고
#    수집됐다고 보면 안 된다** — 200건 넘게 빠진 상태가 그렇게 보인다.
#
# 🔑 **코드 목록은 추측하지 말고 포털에서 받아온다** (2026-08-13 발견):
#     https://info.childcare.go.kr/info_html5/cera/common/areaCodeList.jsp?flag=sigungu&arCode=<시도>
#   시도코드는 `areaSearchList.jsp` 의 `<select id="ctprvn">` 에 있다. **제주는 49000 이다**
#   (표준 50). `scripts/fetch_childcare_arcode.py` 가 이 표를 받아 아래 오버라이드와 대조한다.
#   ⚠️ 이 조회는 **API 호출 한도를 안 쓴다** — 코드를 스캔으로 찾지 말 것.
ARCODE_OVERRIDE = {
    "41463": "41462",   # 용인시기흥구
    "41465": "41463",   # 용인시수지구 (포털 연번이 한 칸씩 밀려 표준 41465 는 빈다)
    "44133": "44132",   # 천안시서북구
    # 포항은 포털에 **구 단위 코드가 없다** — 47110 하나뿐이다(228건). 남·북 둘 다 같은 코드로
    # 받아서 좌표 PIP 가 가른다. 같은 응답을 두 번 받지만 `ingest` 의 stcode 중복제거가 막는다.
    "47111": "47110",   # 포항시남구
    "47113": "47110",   # 포항시북구
    # ─ 2026-08-13 해소분 ─ 포털은 **시 승격·신설 이전 코드나 자체 연번**을 그대로 쓴다.
    "41670": "41730",   # 여주시   (옛 여주군 코드)
    "43745": "43780",   # 증평군   (2003 신설 → 음성 43770 뒤에 연번으로 붙였다)
    "44250": "44515",   # 계룡시   (2003 신설 · 충남 표에서 논산 44230 다음 자리)
    "44270": "44830",   # 당진시   (2012 시 승격 → 태안 44825 뒤에 붙였다)
    "50110": "49110",   # 제주시   (포털 제주 시도코드가 49000 이다)
    "50130": "49130",   # 서귀포시
}
# 포털 코드를 못 찾은 시군구. **비어 있는 것이 정상**이다 (2026-08-13 전부 해소).
# 여기 뭔가 들어 있는 동안은 그 지역이 통째로 빈다 — `meta.partial=true` 로 발행이 막힌다.
# ⚠️ 새로 생기면 **스캔하지 말고** 위 areaCodeList.jsp 에서 코드를 받아 확인할 것.
ARCODE_UNRESOLVED: dict[str, str] = {}
# 연속으로 이만큼 0건이면 코드 문제가 아니라 **호출 한도**를 의심한다.
# 한도 초과도 INFO-200 으로 와서 0건과 구분이 안 된다(2026-08-12 실측) — 그래서 '연속'으로 센다.
EMPTY_STREAK_ABORT = 10


# --------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------

def load_key() -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("CHILDCARE_KEY=") and s.split("=", 1)[1].strip():
            return s.split("=", 1)[1].strip()
    return None


def read_json_safe(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def sgg_codes() -> list[tuple[str, str]]:
    """현행 시군구 코드 목록 (code_map.json · 팀원1 산출물). 256건."""
    p = PROCESSED / "code_map.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return [(x["code"], x["name"]) for x in d.get("sgg", [])]


def legacy_alts() -> dict[str, list[str]]:
    """현행 → 구코드 역인덱스.

    ⚠️ 2026-07-01 전남광주통합으로 광주(29)·전남(46) 이 12 로 바뀌었는데, 정부 API 상당수가
       아직 구코드를 받는다. 현행 코드로 0건이면 구코드로 한 번 더 물어본다.
       (이 재시도가 없으면 호남권이 통째로 비는데, **0건은 조용해서 안 드러난다.**)
    """
    p = PROCESSED / "code_legacy_map.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    rev: dict[str, list[str]] = defaultdict(list)
    for old, new in (d.get("sgg") or {}).items():
        rev[new].append(old)
    return dict(rev)


# --------------------------------------------------------------------------
# 호출·파싱
# --------------------------------------------------------------------------

def fetch(key: str, arcode: str, tries: int = 4) -> str:
    url = f"{API}?key={key}&arcode={arcode}"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"arcode={arcode} 호출 실패: {type(last).__name__}: {last}")


def parse_items(xml: str) -> list[dict]:
    out = []
    for chunk in re.findall(r"<item>(.*?)</item>", xml, re.S):
        out.append(dict(re.findall(r"<(\w+)>([^<]*)</\1>", chunk)))
    return out


def is_dummy(items: list[dict]) -> bool:
    """개발키 판별. 값이 01,02,03… 순번이면 더미다."""
    if not items:
        return False
    it = items[0]
    return [it.get("sidoname"), it.get("sigunname"), it.get("stcode")] == ["01", "02", "03"]


def num(v) -> int:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def coords(item: dict) -> tuple[float, float] | None:
    """(lng, lat) 반환. la=위도 / lo=경도 지만 뒤집혀 오는 경우를 대비해 범위로 검증한다."""
    try:
        lat, lng = float(item.get("la", "")), float(item.get("lo", ""))
    except (TypeError, ValueError):
        return None
    if 124.0 <= lat <= 132.0 and 33.0 <= lng <= 39.0:   # 뒤집혀 왔다
        lat, lng = lng, lat
    if not (124.0 <= lng <= 132.0 and 33.0 <= lat <= 39.0):
        return None
    return lng, lat


# --------------------------------------------------------------------------

def fixture_xy() -> tuple[float, float] | None:
    """더미 응답은 좌표까지 순번(19, 20)이라 PIP 경로가 안 돌아간다.
    점검 때만 기존 POI 의 실제 좌표 하나를 빌려 끼운다 — **수집 경로에는 절대 안 쓴다.**"""
    for p in sorted(PROCESSED.glob("poi_*.json")):
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        # poi_<sgg>.json 은 {meta, school, academy, ...} 형태이고 각 값이 POI 리스트다
        groups = rows.values() if isinstance(rows, dict) else [rows]
        for g in groups:
            for r in g if isinstance(g, list) else []:
                if isinstance(r, dict) and r.get("lat") and r.get("lng"):
                    return float(r["lng"]), float(r["lat"])
    return None


def ingest(items, used, agg, rep, pois, idx, lg, fx_xy, seen) -> None:
    """item 목록을 행정동 집계에 넣는다. 수집 경로와 --from-raw 재집계가 같은 코드를 쓴다.

    `seen` 은 stcode 집합이다. **같은 시설을 두 번 세지 않기 위한 유일한 방어선**이다 —
    포털 코드와 표준코드가 어긋나는 시군구가 있어(ARCODE_OVERRIDE) 인접 코드가 같은
    시설을 함께 주는 상황이 실제로 가능하다. 2026-08-12 전국 수집에서는 중복 0건이었다.
    """
    for it in items:
        st = it.get("stcode") or ""
        if st and st in seen:
            rep["dup"] = rep.get("dup", 0) + 1
            continue
        if st:
            seen.add(st)
        if any(s in (it.get("crstatusname") or "") for s in CLOSED_STATUS):
            rep["closed"] += 1
            continue
        xy = coords(it) or fx_xy
        if not xy:
            rep["no_coord"] += 1
            continue
        adm = idx.lookup(xy[0], xy[1], sgg_hint=None if fx_xy else lg.fix_sgg(used))
        if not adm:
            rep["no_adm"] += 1
            continue
        rep["placed"] += 1
        d = agg[adm]
        d["daycare_cnt"] += 1
        d["daycare_kids"] += num(it.get("crchcnt"))
        d["daycare_capacity"] += num(it.get("crcapat"))
        for f, age in AGE_FIELDS.items():
            d[f"daycare_age_{age}"] += num(it.get(f))
        d["daycare_age_unassigned"] += sum(num(it.get(f)) for f in MIX_FIELDS)
        pois[adm[:5]].append({
            "name": it.get("crname", ""), "type": "어린이집",
            "lat": round(xy[1], 6), "lng": round(xy[0], 6),
            "adm_cd": adm, "kids": num(it.get("crchcnt")),
        })


def blank_agg() -> dict:
    d = {"daycare_cnt": 0, "daycare_kids": 0, "daycare_capacity": 0,
         "daycare_age_unassigned": 0}
    d.update({f"daycare_age_{n}": 0 for n in range(6)})
    return d


def save(a, agg, rep, pois) -> None:
    """childcare.json + poi_childcare_<sgg>.json 저장. 수집·재집계가 같은 경로를 쓴다."""
    # 미해결 코드가 남아 있으면 **저장물에 박아 둔다.** 로그는 흘러가고 파일은 남는다 —
    # 다음 사람이 "제주에 어린이집이 0개"를 보고 원인을 찾을 수 있어야 한다.
    unresolved = sorted(ARCODE_UNRESOLVED)
    obj = {
        "meta": {
            "sample": False,
            "updated": date.today().isoformat(),
            "base_ym": a.ym,
            "source": "어린이집 정보공개포털 보육정보공개 API (cpmsapi030)",
            "source_url": "https://info.childcare.go.kr",
            "age_basis": "CHILD_CNT_00~05 = **만** 0~5세 현원. 혼합·특수반은 unassigned.",
            "note": "유치원(교육부)과 배타적 원천이라 합산해도 중복이 아니다.",
            "partial": bool(unresolved),
            "unresolved_sgg": {c: ARCODE_UNRESOLVED[c] for c in unresolved},
            "report": rep,
        },
        "data": dict(sorted(agg.items())),
    }
    out = PROCESSED / "childcare.json"
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[저장] {out}  ({len(agg):,}개 동)")
    # ⚠️ `data/processed/poi_*.json` 은 **시군구 POI 묶음**(own/competitor/english_kinder 키를
    #    가진 dict)이라는 규약이 있고 verify_c8 가 그걸 검사한다. 어린이집 POI 는 모양이
    #    다른 단순 리스트라 같은 이름 규칙을 쓰면 검증이 FAIL 한다(2026-08-12 실측).
    #    그래서 하위 폴더로 뺀다 — deploy_pages 의 poi_*.json 글롭에도 안 걸린다.
    poi_dir = PROCESSED / "poi_childcare"
    poi_dir.mkdir(parents=True, exist_ok=True)
    for sgg, rows in pois.items():
        (poi_dir / f"{sgg}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"[저장] POI {len(pois)}개 시군구 → {poi_dir}")
    if unresolved:
        print()
        print(f"  [⚠️ 부분수집] 포털 코드를 못 찾은 시군구 {len(unresolved)}개가 **통째로 비어 있다**:")
        print(f"     {', '.join(f'{c} {ARCODE_UNRESOLVED[c]}' for c in unresolved)}")
        print("     meta.partial=true 로 표시했다. 이 상태로 발행하면 해당 지역이 '어린이집 0개'로 보인다.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="어린이집 수집 (정보공개포털)")
    ap.add_argument("--fixture", action="store_true",
                    help="개발키 더미로 파싱·집계 경로만 점검한다 (processed 안 씀)")
    ap.add_argument("--sgg", default="", help="쉼표로 구분한 시군구코드. 비우면 전국")
    ap.add_argument("--ym", default=date.today().strftime("%Y%m"))
    ap.add_argument("--sleep", type=float, default=0.2, help="호출 간 간격(초)")
    ap.add_argument("--stat", action="store_true", help="수집 없이 현황만")
    ap.add_argument("--from-raw", action="store_true",
                    help="API 를 부르지 않고 data/raw 의 XML 전부로 다시 집계한다")
    ap.add_argument("--retry-missing", action="store_true",
                    help="아직 못 받은 시군구만 다시 시도한다 (미해결 + 코드 교정분)")
    a = ap.parse_args(argv)

    raw_dir = RAW / a.ym
    if a.stat:
        n = len(list(raw_dir.glob("*.xml"))) if raw_dir.exists() else 0
        print(f"[stat] {raw_dir} · 원본 {n}개")
        out = PROCESSED / "childcare.json"
        print(f"[stat] {out.name}: {'있음' if out.exists() else '없음'}")
        return 0

    # ---- 원본 재집계 (API 미사용) ----------------------------------------
    # 부분 재수집 뒤 이 모드로 다시 돌리는 것이 **JSON 병합보다 안전하다.** 원본이 다 있고
    # stcode 중복 제거가 한 번에 걸리므로, 경계 넘어온 시설이 이중계상될 여지가 없다.
    #   ① 빠진 시군구만:  python scripts/collect_childcare.py --sgg 47110,50110
    #   ② 전량 재집계:    python scripts/collect_childcare.py --from-raw
    # ⚠️ ①만 하고 끝내면 childcare.json 이 **그 시군구만 남은 파일로 덮인다.** 반드시 ②까지.
    if a.from_raw:
        files = sorted(raw_dir.glob("*.xml"))
        if not files:
            print(f"[!!] 원본이 없다: {raw_dir}")
            return 1
        idx = AdmIndex.load(verbose=True)
        lg = LegacyMap.load()
        agg = defaultdict(blank_agg)
        rep = {"sgg_total": len(files), "sgg_empty": [], "sgg_legacy_hit": [],
               "facilities": 0, "closed": 0, "no_coord": 0, "no_adm": 0, "placed": 0,
               "from_raw": True}
        pois = defaultdict(list)
        seen: set[str] = set()
        print(f"[재집계] 원본 {len(files)}개 · {raw_dir}")
        for f in files:
            items = parse_items(f.read_text(encoding="utf-8", errors="replace"))
            if not items:
                # 오버라이드가 걸린 표준코드 원본은 **비어 있는 게 정상**이다 — 그 지역은
                # 포털 코드(ARCODE_OVERRIDE 의 값)로 받은 다른 파일에 들어 있다.
                # 이걸 sgg_empty 로 세면 --retry-missing 이 매일 같은 11곳을 헛돈다.
                (rep.setdefault("sgg_superseded", []) if f.stem in ARCODE_OVERRIDE
                 else rep["sgg_empty"]).append(f.stem)
                continue
            rep["facilities"] += len(items)
            ingest(items, f.stem, agg, rep, pois, idx, lg, None, seen)
        print(f"[집계] 행정동 {len(agg):,}개 · 시설 {rep['placed']:,} 배정 "
              f"· 중복제거 {rep.get('dup', 0):,}")
        if rep["sgg_empty"]:
            print(f"       ※ 빈 원본 {len(rep['sgg_empty'])}개: {rep['sgg_empty'][:10]}")
        if rep.get("sgg_superseded"):
            print(f"       (오버라이드로 대체된 표준코드 원본 {len(rep['sgg_superseded'])}개 — 정상)")
        save(a, agg, rep, pois)
        return 0

    key = load_key()
    if not key:
        print("[!!] .env 의 CHILDCARE_KEY 가 비어 있다. scripts/check_childcare_key.py 참고")
        return 1

    if a.retry_missing:
        # 못 받은 곳 = ①포털 코드 미상(ARCODE_UNRESOLVED) ②코드가 어긋나 다시 물어야 하는 곳
        # (ARCODE_OVERRIDE 키 — 표준코드로 받아 놓은 raw 는 다른 구의 것이라 그대로 두면 빈다)
        # ③지난 산출물이 0건으로 기록한 곳. 목록을 손으로 관리하지 않기 위해 코드에서 만든다.
        names = dict(sgg_codes())
        want = set(ARCODE_UNRESOLVED) | set(ARCODE_OVERRIDE)
        prev = read_json_safe(PROCESSED / "childcare.json")
        for row in ((prev.get("meta") or {}).get("report") or {}).get("sgg_empty") or []:
            want.add(str(row).split()[0])
        targets = [(c, names.get(c, "")) for c in sorted(want)]
        print(f"[재시도] 못 받은 시군구 {len(targets)}개: "
              f"{', '.join(f'{c}{(' ' + names[c]) if c in names else ''}' for c in sorted(want))}")
    else:
        targets = ([(c, "") for c in a.sgg.split(",") if c.strip()] if a.sgg
                   else sgg_codes())
    alts = legacy_alts()
    print(f"[대상] 시군구 {len(targets)}개 · 원본 → {raw_dir}")

    # ---- 첫 호출로 개발키/운영키를 먼저 가른다. 더미면 여기서 멈춘다 ----
    probe_code = targets[0][0]
    probe = parse_items(fetch(key, probe_code))
    dummy = is_dummy(probe)
    if dummy and not a.fixture:
        print()
        print("  [중단] **개발키다 — 값이 전부 더미(01, 02, 03 …)이고 arcode 도 무시된다.**")
        print("         이 상태로 수집하면 전국이 같은 값 1건으로 채워진다.")
        print("         → 운영계정 승인 후 운영키로 다시 돌릴 것.")
        print("         구조만 점검하려면:  python scripts/collect_childcare.py --fixture")
        return 2
    fx_xy = None
    if dummy:
        print("  [fixture] 더미 응답으로 파싱·집계 경로만 점검한다. processed 는 쓰지 않는다.")
        targets = targets[:1]
        fx_xy = fixture_xy()
        print(f"  [fixture] 좌표도 더미(19,20)라 PIP 가 안 돌아간다 → 실좌표 {fx_xy} 를 빌려 끼운다.")

    idx = AdmIndex.load(verbose=True)
    lg = LegacyMap.load()

    raw_dir.mkdir(parents=True, exist_ok=True)
    agg: dict[str, dict] = defaultdict(blank_agg)
    rep = {"sgg_total": len(targets), "sgg_empty": [], "sgg_legacy_hit": [],
           "facilities": 0, "closed": 0, "no_coord": 0, "no_adm": 0, "placed": 0}
    pois: dict[str, list] = defaultdict(list)
    seen: set[str] = set()

    empty_streak = 0
    for i, (code, name) in enumerate(targets, 1):
        # 포털이 다른 코드를 쓰는 시군구는 바꿔서 묻는다 (ARCODE_OVERRIDE 주석 참조)
        ask = ARCODE_OVERRIDE.get(code, code)
        xml = fetch(key, ask)
        items = parse_items(xml)
        used = ask
        # 0건이면 **한 번 더 물어본다.** 포털은 일시적으로도 INFO-200(0건)을 준다 —
        # 2026-08-12 전국 수집에서 빈 10곳 중 일부가 코드 불일치가 아니라 이 단발 실패였다.
        # 재시도 없이 기록하면 그 시군구가 '어린이집 0개'로 굳는다. 비용은 0건일 때 1회뿐이다.
        if not items:
            time.sleep(2.0)
            xml_retry = fetch(key, ask)
            if parse_items(xml_retry):
                xml, items = xml_retry, parse_items(xml_retry)
                rep["retry_hit"] = rep.get("retry_hit", 0) + 1

        # 현행 코드로 0건이면 구코드로 한 번 더 (전남광주통합)
        if not items and code in alts:
            for old in alts[code]:
                xml2 = fetch(key, old)
                if parse_items(xml2):
                    xml, items, used = xml2, parse_items(xml2), old
                    rep["sgg_legacy_hit"].append(f"{code}<-{old}")
                    break
        (raw_dir / f"{used}.xml").write_text(xml, encoding="utf-8")
        if not items:
            rep["sgg_empty"].append(f"{code} {name}")
            # 한도 초과도 `INFO-200 검색결과가 없습니다` 로 온다 — 0건과 본문이 같다.
            # 코드 불일치는 흩어져 나오지만 한도는 그 시점부터 **연속으로** 나온다.
            empty_streak += 1
            if empty_streak >= EMPTY_STREAK_ABORT:
                print()
                print(f"  [중단] 0건이 {empty_streak}회 **연속**이다 — 호출 한도 초과를 의심한다.")
                print("         (한도 초과 응답은 0건과 본문이 같아 구분되지 않는다 · 2026-08-12 실측)")
                print("         이대로 두면 남은 시군구가 전부 '어린이집 0개'로 저장된다.")
                print("         → 한도가 풀린 뒤 다시 돌릴 것. 확인:  python scripts/check_childcare_key.py")
                return 3
        else:
            empty_streak = 0
        rep["facilities"] += len(items)
        ingest(items, used, agg, rep, pois, idx, lg, fx_xy, seen)

        if i % 25 == 0 or i == len(targets):
            print(f"  {i}/{len(targets)} · 시설 {rep['facilities']:,} · 배정 {rep['placed']:,}")
        time.sleep(a.sleep)

    print()
    print(f"[집계] 행정동 {len(agg):,}개 · 시설 {rep['placed']:,} 배정")
    print(f"       폐지·휴지 {rep['closed']:,} · 좌표없음 {rep['no_coord']:,} · 동미배정 {rep['no_adm']:,}")
    if rep["sgg_empty"]:
        print(f"       ※ 0건 시군구 {len(rep['sgg_empty'])}개 — 조용한 실패일 수 있다: "
              f"{rep['sgg_empty'][:5]}")
    if rep["sgg_legacy_hit"]:
        print(f"       구코드로 잡힌 시군구 {len(rep['sgg_legacy_hit'])}개")

    if a.fixture:
        print("\n  [fixture] 여기까지가 점검이다. childcare.json 은 쓰지 않았다.")
        sample = next(iter(agg.items()), None)
        if sample:
            print(f"  샘플 집계: {sample[0]} -> {sample[1]}")
        return 0

    # ⚠️ **부분 수집은 집계 파일을 쓰지 않는다.**
    #    --sgg / --retry-missing 은 대상 시군구만 도니, 그대로 저장하면 childcare.json 이
    #    "그 몇 개만 있는 파일"로 덮인다. 전국 산출물이 몇 개 동짜리로 줄어드는 사고가
    #    조용히 난다(2026-08-12 실제로 날 뻔했다 — 한도 상태에서 11개 재시도가 전부 0건이었다).
    #    부분 수집의 역할은 **원본 XML 갱신까지**이고, 집계는 --from-raw 가 전량으로 한다.
    if a.sgg or a.retry_missing:
        print()
        print("  [원본만 갱신] 부분 수집이라 childcare.json 은 건드리지 않았다.")
        print(f"     받은 시설 {rep['facilities']:,}건 · 원본 {raw_dir}")
        print("     집계는 전량으로:  python scripts/collect_childcare.py --from-raw")
        return 0

    save(a, agg, rep, pois)
    return 0


if __name__ == "__main__":
    sys.exit(main())
