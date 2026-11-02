# -*- coding: utf-8 -*-
"""
collect_population.py  —  지역분석 프로젝트 / 팀원2 (인구 데이터)

산출물 (DATA_CONTRACT.md §2-3 준수)
  data/processed/population.json        키 = 행정동코드 8자리
  data/processed/population_sgg.json    키 = 시군구코드 5자리
  data/processed/population_sido.json   키 = 시도코드 2자리

데이터 소스
  [1순위] 공공데이터포털 OpenAPI (행안부 주민등록 인구/세대현황, 데이터 15108065)
          - 인증키는 .env 의 DATA_GO_KR_KEY 에서 읽는다 (하드코딩 금지).
          - 다만 이 API는 총인구/세대수만 주고 **연령 세분(1세 단위)이 없다.**
            따라서 본 스크립트에서는 보조/검증용이며, 기본 파이프라인은 [2순위]다.
  [2순위 = 실질 정본] 행안부 주민등록 인구통계 jumin.mois.go.kr
          - https://jumin.mois.go.kr/ageStatMonth.do  (행정동별 연령별 인구현황)
          - https://jumin.mois.go.kr/statMonth.do     (행정동별 인구·세대현황)
          - 공개 API는 없지만 다운로드 버튼이 평범한 POST form 이라 자동화 가능하다.
            아래 download_jumin_age() / download_jumin_stat() 참고. (2026-07-23 동작 확인)

────────────────────────────────────────────────────────────────────────
■ 자동 다운로드가 실패할 때의 수동 다운로드 절차 (README 용 주석)
────────────────────────────────────────────────────────────────────────
사이트가 개편되어 POST 파라미터가 바뀌면 아래처럼 손으로 받아 data/raw/population/ 에
넣으면 그대로 파싱된다. 파일명 규칙만 지키면 된다.

  (A) 연령별 인구  →  data/raw/population/age_<YYYYMM>.csv
      1. https://jumin.mois.go.kr/ageStatMonth.do 접속
      2. [행정구역] 전체 / [등록구분] 전체
      3. [조회기간] 월간, 시작=종료=대상 년월 (예: 2026년 06월)
      4. [연령 구분단위] "1세"  (5세/10세도 파싱되지만 근사배분이 들어간다)
      5. [연령 조회범위] 0세 ~ 100세 이상
      6. "남/여 구분" 체크 해제(계만), "계" 체크 유지
      7. 검색 → 하단 [CSV 다운로드] → 라디오 "전체읍면동현황" 선택 → 다운로드
      8. 받은 파일을 age_202606.csv 로 이름 바꿔 data/raw/population/ 에 저장 (인코딩 CP949 그대로 두면 됨)

  (B) 인구·세대현황(세대수 + 12개월 추세)  →  data/raw/population/stat_<YYYYMM>.csv
      1. https://jumin.mois.go.kr/statMonth.do 접속
      2. [행정구역] 전체
      3. [조회기간] 월간, 시작 = 대상년월-11개월, 종료 = 대상년월 (12개월)
      4. "세대수" 항목 체크
      5. 검색 → [CSV 다운로드] → "전체읍면동현황" → 다운로드
      6. stat_202606.csv 로 이름 바꿔 data/raw/population/ 에 저장

  그 뒤:  python scripts\\collect_population.py --ym 202606 --no-download
────────────────────────────────────────────────────────────────────────
■ 코드 체계 변환 (DATA_CONTRACT §1-1)
────────────────────────────────────────────────────────────────────────
2026-07-01 전남광주통합특별시(12) 출범 등으로 행안부 주민등록 통계(202606)의 구코드
(29 광주 / 46 전남 / 개편 전 인천 자치구)는 현행 경계 코드와 다르다.
→ data/processed/code_legacy_map.json (팀원1, 읽기 전용) 으로 변환한 뒤 산출한다.
   순서: ① 출장소 분리 → ② 코드 변환 → ③ 출장소 인구를 소속 행정동에 합산 → ④ 시군구·시도 재집계
   변환 전후 전국 총인구가 다르면 즉시 예외를 던진다(이중계상/누락 방지).
   변환표가 비워둔 미매칭(안양8·9동 등)은 추측으로 채우지 않고 meta.legacy_conversion 에 기록한다.
────────────────────────────────────────────────────────────────────────

CLI
  --sample            인증키/네트워크 없이 형식이 동일한 전국 규모 더미 3종 생성 (meta.sample=true)
  --ym YYYYMM         기준 년월 (기본: 자동 추정 = 전월)
  --out DIR           출력 디렉터리 (기본: data/processed)
  --no-download       네트워크 접근 없이 data/raw/population/ 의 기존 파일만 파싱
  --raw DIR           원본 저장/탐색 디렉터리 (기본: data/raw/population)

실행 (Windows)
  C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python312\\python.exe scripts\\collect_population.py --ym 202606
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:  # --sample 은 requests 없이도 돌아야 한다
    requests = None

# ────────────────────────────── 경로 ──────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "population"
OUT_DIR = ROOT / "data" / "processed"
CODE_MAP = OUT_DIR / "code_map.json"          # 팀원1 산출물 (없을 수 있음)
LEGACY_MAP = OUT_DIR / "code_legacy_map.json"  # 팀원1 산출물 — 구코드→현행코드 변환표 (읽기 전용)

JUMIN = "https://jumin.mois.go.kr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# DATA_CONTRACT §2-3 연령 구간
AGE_BUCKETS = [
    ("0_5", 0, 5),
    ("6_11", 6, 11),
    ("12_14", 12, 14),
    ("15_17", 15, 17),
    ("18_64", 18, 64),
    ("65_", 65, 200),
]
AGE_DETAIL_YEARS = tuple(range(18))
SCHOOL_AGE_MIN = 3
SCHOOL_AGE_MAX = 16


# ────────────────────────────── 공통 유틸 ──────────────────────────────
def load_env(path: Path | None = None) -> dict:
    """.env 를 읽어 dict 로. (python-dotenv 의존 없이 최소 파서)"""
    env = {}
    p = path or (ROOT / ".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    # 실제 프로세스 환경변수가 우선
    for k in ("DATA_GO_KR_KEY",):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def prev_month_ym(today: date | None = None) -> str:
    d = today or date.today()
    y, m = d.year, d.month - 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y}{m:02d}"


def ym_minus(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    total = y * 12 + (m - 1) - n
    return f"{total // 12}{total % 12 + 1:02d}"


def ym_range(end_ym: str, months: int = 12) -> list[str]:
    return [ym_minus(end_ym, i) for i in range(months - 1, -1, -1)]


def to_int(s: str) -> int:
    s = (s or "").replace(",", "").replace('"', "").strip()
    if not s or s in ("-", "*"):
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def decode_csv(raw: bytes) -> str:
    """jumin.mois.go.kr CSV 는 CP949. 혹시 UTF-8(BOM)로 바뀌어도 견디게."""
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")


def level_of(code10: str) -> str:
    """행안부 10자리 코드에서 레벨 판정."""
    if code10.endswith("00000000"):
        return "sido"
    if code10.endswith("00000"):
        return "sgg"
    return "adm"


def split_name(cell: str):
    """'서울특별시 종로구 청운효자동(1111051500)' → (name, code10)"""
    m = re.search(r"\(?\s*(\d{10})\s*\)?\s*$", cell.strip())
    if not m:
        return None, None
    code = m.group(1)
    name = cell[: m.start()].replace("(", "").strip()
    return name, code


def short_name(full_name: str, level: str) -> str:
    """'서울특별시 종로구 청운효자동' → '청운효자동' (계약서 name 은 해당 레벨 명칭)"""
    parts = [p for p in full_name.split() if p]
    if not parts:
        return full_name
    return parts[-1]


# ────────────────────────────── code_map (팀원1) ──────────────────────────────
def load_legacy_map() -> dict | None:
    """팀원1 제공 구코드→현행코드 변환표 (읽기 전용)."""
    if not LEGACY_MAP.exists():
        return None
    try:
        return json.loads(LEGACY_MAP.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] code_legacy_map.json 읽기 실패: {e}", file=sys.stderr)
        return None


def load_code_map() -> dict | None:
    if not CODE_MAP.exists():
        return None
    try:
        return json.loads(CODE_MAP.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] code_map.json 읽기 실패: {e}", file=sys.stderr)
        return None


# ────────────────────────────── 1순위: 공공데이터포털 API ──────────────────────────────
DATA_GO_KR_ENDPOINT = (
    "https://apis.data.go.kr/1741000/admmSexdAgePopulation/"
    "getAdmmSexdAgePopulation"  # 데이터 15108065 계열. 오퍼레이션명은 승인 후 상세페이지 확인 필요.
)


def fetch_datagokr(ym: str, key: str, timeout: int = 30):
    """
    1순위 소스 시도. 인증키가 있어야 하고, 데이터셋별 오퍼레이션 경로가 승인 후에야 확정된다.
    현재(키 미발급) 검증 불가 → 실패 시 조용히 None 을 돌려주고 2순위(jumin)로 폴백한다.
    반환: {adm_cd8: {"total": int, "households": int}} 또는 None
    """
    if not key or requests is None:
        return None
    try:
        out, page = {}, 1
        while True:
            r = requests.get(
                DATA_GO_KR_ENDPOINT,
                params={
                    "serviceKey": key,
                    "type": "JSON",
                    "stdgTm": ym,
                    "pageNo": page,
                    "numOfRows": 1000,
                },
                timeout=timeout,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            js = r.json()
            rows = _dig_rows(js)
            if not rows:
                break
            for row in rows:
                code = str(row.get("admmCd") or row.get("adm_cd") or "")
                if len(code) < 8:
                    continue
                out[code[:8]] = {
                    "total": to_int(str(row.get("totNmprCnt") or row.get("total") or 0)),
                    "households": to_int(str(row.get("hshldCnt") or row.get("households") or 0)),
                }
            if len(rows) < 1000:
                break
            page += 1
        return out or None
    except Exception as e:
        print(f"[info] data.go.kr API 사용 불가 → jumin 폴백 ({e})", file=sys.stderr)
        return None


def _dig_rows(js):
    """공공데이터포털 응답의 item 배열을 관대하게 찾아낸다."""
    if isinstance(js, list):
        return js
    if not isinstance(js, dict):
        return []
    for k in ("row", "items", "item", "data"):
        if k in js:
            v = js[k]
            return v if isinstance(v, list) else [v]
    for v in js.values():
        r = _dig_rows(v)
        if r:
            return r
    return []


# ────────────────────────────── 2순위: jumin.mois.go.kr 자동 다운로드 ──────────────────────────────
def _jumin_post(session, page: str, action: str, query: dict, form: dict, timeout=420) -> bytes:
    session.get(f"{JUMIN}/{page}", timeout=60, headers={"User-Agent": UA})  # 세션 쿠키 획득
    q = "&".join(f"{k}={v}" for k, v in query.items())
    r = session.post(
        f"{JUMIN}/{action}?{q}",
        data=form,
        timeout=timeout,
        headers={"User-Agent": UA, "Referer": f"{JUMIN}/{page}"},
    )
    r.raise_for_status()
    ct = (r.headers.get("Content-Type") or "").lower()
    if "text/html" in ct and b"<html" in r.content[:2000].lower():
        raise RuntimeError("HTML 응답 (다운로드 실패 — 파라미터 변경 가능성). 수동 절차 주석 참고")
    return r.content


def download_jumin_age(ym: str, raw_dir: Path, arg_type: str = "1") -> Path:
    """
    행정동별 연령별 인구현황 CSV (전체 읍면동, 1세 단위) 다운로드.
    POST /downloadCsvAge.do?searchYearMonth=month&xlsStats=3
      xlsStats  1=현재화면 / 2=전체시군구현황 / 3=전체읍면동현황
      sltArgTypes 1=1세 / 5=5세 / 10=10세
      gender 파라미터를 넘기지 않으면 '계'만 내려온다.
    """
    if requests is None:
        raise RuntimeError("requests 미설치")
    s = requests.Session()
    form = {
        "sltOrgType": "1", "sltOrgLvl1": "A", "sltOrgLvl2": "",
        "sum": "sum", "sltUndefType": "",
        "searchYearStart": ym[:4], "searchMonthStart": ym[4:],
        "searchYearEnd": ym[:4], "searchMonthEnd": ym[4:],
        "sltOrderType": "1", "sltOrderValue": "ASC",
        "sltArgTypes": arg_type, "sltArgTypeA": "0", "sltArgTypeB": "100",
        "category": "month",
    }
    content = _jumin_post(
        s, "ageStatMonth.do", "downloadCsvAge.do",
        {"searchYearMonth": "month", "xlsStats": "3"}, form,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / f"age_{ym}.csv"
    p.write_bytes(content)
    return p


def download_jumin_stat(ym: str, raw_dir: Path, months: int = 12) -> Path:
    """
    행정동별 인구·세대현황 CSV (전체 읍면동, 최근 months 개월) 다운로드.
    POST /downloadCsv.do?searchYearMonth=month&xlsStats=3   category=monthDetail(읍면동 월간)
    """
    if requests is None:
        raise RuntimeError("requests 미설치")
    start = ym_minus(ym, months - 1)
    s = requests.Session()
    form = {
        "sltOrgType": "1", "sltOrgLvl1": "A", "sltOrgLvl2": "",
        "generation": "generation", "sltUndefType": "",
        "searchYearStart": start[:4], "searchMonthStart": start[4:],
        "searchYearEnd": ym[:4], "searchMonthEnd": ym[4:],
        "sltOrderType": "1", "sltOrderValue": "ASC",
        "category": "monthDetail",
    }
    content = _jumin_post(
        s, "statMonth.do", "downloadCsv.do",
        {"searchYearMonth": "month", "xlsStats": "3"}, form,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / f"stat_{ym}.csv"
    p.write_bytes(content)
    return p


# ────────────────────────────── 파서 ──────────────────────────────
AGE_COL_RE = re.compile(r"_(\d+)세")
AGE_100_RE = re.compile(r"100세\s*이상")


def parse_age_csv(path: Path):
    """
    연령별 인구 CSV → ({code10: {"name":.., "total":.., "age":{버킷:수}}}, approx_info)
    1세 단위면 정확 합산, 5세/10세 단위면 균등 근사 배분 후 approx=True.
    """
    text = decode_csv(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]

    # 연령 컬럼 인덱스 → (시작나이, 끝나이)
    age_cols: list[tuple[int, int, int]] = []
    total_idx = None
    for i, h in enumerate(header):
        h = h.strip()
        if "총인구수" in h and total_idx is None:
            total_idx = i
            continue
        if "연령구간인구수" in h:
            continue
        if AGE_100_RE.search(h):
            age_cols.append((i, 100, 200))
            continue
        m = re.search(r"_(\d+)\s*~\s*(\d+)\s*세", h)
        if m:
            age_cols.append((i, int(m.group(1)), int(m.group(2))))
            continue
        m = AGE_COL_RE.search(h)
        if m:
            a = int(m.group(1))
            age_cols.append((i, a, a))

    if not age_cols:
        raise RuntimeError(f"{path.name}: 연령 컬럼을 찾지 못했습니다. 헤더 확인 필요")

    approx = any(hi > lo for _, lo, hi in age_cols if hi < 200)
    unit = 1 if not approx else max(hi - lo + 1 for _, lo, hi in age_cols if hi < 200)

    out = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name, code = split_name(row[0])
        if not code:
            continue
        buckets = {k: 0.0 for k, _, _ in AGE_BUCKETS}
        years = {str(a): 0.0 for a in AGE_DETAIL_YEARS}
        for idx, lo, hi in age_cols:
            if idx >= len(row):
                continue
            v = to_int(row[idx])
            if v == 0:
                continue
            span = hi - lo + 1
            for bname, blo, bhi in AGE_BUCKETS:
                ov = min(hi, bhi) - max(lo, blo) + 1     # 구간 겹침
                if ov > 0:
                    buckets[bname] += v * (ov / span)     # 균등 배분(1세 단위면 ov==span==1)
            for a in range(max(lo, 0), min(hi, AGE_DETAIL_YEARS[-1]) + 1):
                years[str(a)] += v / span
        total = to_int(row[total_idx]) if total_idx is not None and total_idx < len(row) else 0
        ints = {k: int(round(v)) for k, v in buckets.items()}
        if total:  # 반올림 오차를 최대 버킷(18_64)에 흡수시켜 합계 = total 보장
            diff = total - sum(ints.values())
            ints["18_64"] += diff
        out[code] = {
            "name": name,
            "total": total or sum(ints.values()),
            "age": ints,
            "age_year": {k: int(round(v)) for k, v in years.items()},
        }

    return out, {"approx": approx, "unit": unit}


def parse_stat_csv(path: Path):
    """
    인구·세대현황 CSV → {code10: {"name":.., "households":int, "trend12":[...], "months":[...]}}
    헤더가 'YYYY년MM월_총인구수' / 'YYYY년MM월_세대수' 반복 구조.
    """
    text = decode_csv(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]

    pop_cols, hh_cols = [], []
    for i, h in enumerate(header):
        m = re.search(r"(\d{4})년\s*(\d{2})월_(.+)", h.strip())
        if not m:
            continue
        ym = m.group(1) + m.group(2)
        kind = m.group(3).strip()
        if kind == "총인구수":
            pop_cols.append((ym, i))
        elif kind == "세대수":
            hh_cols.append((ym, i))

    pop_cols.sort()
    hh_cols.sort()

    out = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name, code = split_name(row[0])
        if not code:
            continue
        trend, months = [], []
        for ym, i in pop_cols:
            if i < len(row):
                trend.append(to_int(row[i]))
                months.append(ym)
        hh = 0
        if hh_cols:
            _, i = hh_cols[-1]
            if i < len(row):
                hh = to_int(row[i])
        out[code] = {"name": name, "households": hh, "trend12": trend, "months": months}
    return out


# ────────────────────────────── 조립 ──────────────────────────────
def _age_year_fallback(age: dict) -> dict:
    """샘플/구버전 입력용 균등 배분. 실데이터는 1세 단위 age_year 를 직접 사용한다."""
    out = {str(a): 0 for a in AGE_DETAIL_YEARS}
    for bucket, lo, hi in AGE_BUCKETS[:4]:
        value = int(age.get(bucket, 0))
        span = hi - lo + 1
        q, r = divmod(value, span)
        for offset, a in enumerate(range(lo, hi + 1)):
            out[str(a)] = q + (1 if offset < r else 0)
    return out


def build_record(total, households, age, trend12, age_year=None):
    age = {k: int(age.get(k, 0)) for k, _, _ in AGE_BUCKETS}
    years = (_age_year_fallback(age) if age_year is None else
             {str(a): int(age_year.get(str(a), age_year.get(a, 0))) for a in AGE_DETAIL_YEARS})
    rec = {
        "total": int(total),
        "households": int(households or 0),
        "age": age,
        "age_year": years,
        "school_age": sum(years[str(a)] for a in range(SCHOOL_AGE_MIN, SCHOOL_AGE_MAX + 1)),
        "trend12": [int(x) for x in (trend12 or [])],
    }
    t = rec["trend12"]
    rec["trend_pct"] = round((t[-1] - t[0]) / t[0] * 100, 1) if len(t) >= 2 and t[0] else 0.0
    return rec


def merge_rec(dst: dict, src: dict):
    """행정동 레코드 합산 (출장소 → 소속 행정동, 코드 변환 충돌 시 사용)."""
    dst["total"] += src["total"]
    dst["households"] += src["households"]
    for b in dst["age"]:
        dst["age"][b] += src["age"].get(b, 0)
    for a in dst["age_year"]:
        dst["age_year"][a] += (src.get("age_year") or {}).get(a, 0)
    if src.get("trend12"):
        if not dst.get("trend12"):
            dst["trend12"] = list(src["trend12"])
        elif len(dst["trend12"]) == len(src["trend12"]):
            dst["trend12"] = [x + y for x, y in zip(dst["trend12"], src["trend12"])]
    dst["school_age"] = sum(dst["age_year"][str(a)]
                            for a in range(SCHOOL_AGE_MIN, SCHOOL_AGE_MAX + 1))
    t = dst.get("trend12") or []
    dst["trend_pct"] = round((t[-1] - t[0]) / t[0] * 100, 1) if len(t) >= 2 and t[0] else 0.0
    return dst


# 인구 원천이 경계(admdongkor ver20260701)보다 먼저 바뀐 행정동 → 경계에 있는 코드로 되돌린다.
# 관리자 승인 2026-10-12. 경계 새 버전에 해당 코드가 생기면 그 항목을 지울 것(월간점검 ② 경계 버전 참고).
# 집현동처럼 분동은 원래 동에 합산되므로 7월 경계 기준 모습 그대로다.
D1_BRANCH_OFFICES = {"44180329", "48240890", "48270251"}  # build_metrics.BRANCH_OFFICES 와 같은 값

BOUNDARY_LAG = {
    "27710268": "27710380",  # 대구 달성군 구지면 → 구지읍 승격 (코드만 변경, 구역 동일) · 202609 첫 등장
    "36110558": "36110556",  # 세종 집현동 신설 ← 36110556 분동 (13,199명) · 202609 첫 등장
}


def convert_legacy(adm_recs: dict, adm_names: dict, legacy: dict):
    """
    구코드(29 광주 / 46 전남 / 개편 전 인천 등) → 현행 코드 변환.
    DATA_CONTRACT §1-1 개정 (2026-07-01 전남광주통합특별시 출범) 대응.
    변환표는 팀원1 제공 data/processed/code_legacy_map.json (읽기 전용).

    처리 순서 (틀리면 이중계상 난다):
      1) '출장소' 행을 일반 행정동 집합에서 **먼저 분리** — 이 시점에서 이중계상 가능성이 사라진다
      2) 일반 행정동만 코드 변환 (new = legacy["adm"].get(old, old))
      3) 분리해 둔 출장소 인구를 legacy["branch_office"] 가 지정한 **소속 행정동(신코드)에 합산**
         (출장소는 행정동 하위 단위라 경계 데이터에 독립 폴리곤이 없다. 버리면 인구 누락,
          별도 동으로 두면 지도에서 사라진다 → 반드시 합산)
    총인구는 어떤 경로로도 보존된다(합산만 하고 버리지 않음).
    """
    admmap = {**(legacy.get("adm", {}) or {}), **BOUNDARY_LAG}
    office = legacy.get("branch_office", {}) or {}

    # 1) 출장소 분리
    office_rows = {k: v for k, v in adm_recs.items() if k in office}
    normal = {k: v for k, v in adm_recs.items() if k not in office}

    stat = {"identity": 0, "mapped": 0, "collisions": [],
            "office_merged": 0, "office_target_missing": [],
            "office_unmapped": []}

    # 2) 일반 행정동 코드 변환
    new_recs, new_names = {}, {}
    for old, r in normal.items():
        new = admmap.get(old, old)
        stat["mapped" if new != old else "identity"] += 1
        if new in new_recs:                      # 다대일 충돌 (팀원1 검증상 0건)
            stat["collisions"].append(f"{old}->{new}")
            merge_rec(new_recs[new], r)
        else:
            new_recs[new] = r
            new_names[new] = adm_names.get(old, "")

    # 3) 출장소 인구를 소속 행정동에 합산
    for old, r in office_rows.items():
        tgt = office[old]
        if tgt in new_recs:
            merge_rec(new_recs[tgt], r)
            stat["office_merged"] += 1
        else:
            # 소속 행정동이 인구통계에 없음 → 인구를 버리지 않도록 해당 코드로 살려둔다
            stat["office_target_missing"].append(f"{old}->{tgt}")
            new_recs[tgt] = r
            new_names.setdefault(tgt, adm_names.get(old, ""))

    # 변환표에 없는 출장소 → 같은 시군구에서 **바로 아래 코드**의 행정동에 합산 (2026-10-12).
    #   202609 부터 jumin 이 전남 출장소 27개를 신코드(12xxx)로 따로 내보내 변환표(구코드 키)를 비켜갔다.
    #   이 규칙은 기존 branch_office 61건을 61/61 재현한다(돌산읍우두출장소 12130251 → 돌산읍 12130250).
    #   그래도 못 찾으면 구코드 그대로 남아 조인 실패(build_metrics 하드가드 ①)로 드러난다
    for k in list(new_recs):
        # D-1 출장소 3건(원산·신수·삼랑진임천)은 2026-08-04 결정대로 합산하지 않는다 — build_metrics 가 목록에서 뺀다
        if "출장소" in new_names.get(k, "") and k not in office and k not in D1_BRANCH_OFFICES:
            parents = [c for c in new_recs if c[:5] == k[:5] and c < k and "출장소" not in new_names.get(c, "")]
            if parents:
                merge_rec(new_recs[max(parents)], new_recs.pop(k))
                new_names.pop(k, None)
                stat["office_merged"] += 1
            else:
                stat["office_unmapped"].append(f"{k} {new_names.get(k, '')}")

    return new_recs, new_names, stat


def assemble(age_map: dict, stat_map: dict, code_map: dict | None, legacy: dict | None = None):
    """
    행정동 레코드를 만들고 (필요 시) 구코드를 현행 코드로 변환한 뒤,
    시군구·시도는 **변환 후 행정동에서 재집계**한다.
    (원본 CSV 의 시군구/시도 집계행은 구코드 기준이라 통합 시도(12)를 표현할 수 없으므로 쓰지 않는다.
     동시에 '수원시(41110)' 처럼 산하에 구가 또 있는 시 단위 행의 이중계상도 구조적으로 사라진다.)
    """
    lv = {"adm": {}, "sgg": {}, "sido": {}}
    names = {"adm": {}, "sgg": {}, "sido": {}}

    for code10, a in age_map.items():
        if level_of(code10) != "adm":
            continue
        s = stat_map.get(code10, {})
        lv["adm"][code10[:8]] = build_record(a["total"], s.get("households"),
                                             a["age"], s.get("trend12"), a.get("age_year"))
        names["adm"][code10[:8]] = short_name(a["name"], "adm")

    total_before = sum(r["total"] for r in lv["adm"].values())
    conv_stat = None
    if legacy:
        lv["adm"], names["adm"], conv_stat = convert_legacy(lv["adm"], names["adm"], legacy)
        total_after = sum(r["total"] for r in lv["adm"].values())
        conv_stat["total_before"] = total_before
        conv_stat["total_after"] = total_after
        conv_stat["total_preserved"] = (total_before == total_after)
        if total_before != total_after:
            raise RuntimeError(
                f"총인구 보존 실패: 변환 전 {total_before:,} → 변환 후 {total_after:,} "
                f"(차이 {total_after - total_before:+,}). 출장소 합산/코드 변환 경로 점검 필요."
            )

    # 시군구·시도 재집계 (대시보드는 합산하지 않는다 — DATA_CONTRACT §2-3)
    for child, parent, cut in (("adm", "sgg", 5), ("sgg", "sido", 2)):
        agg = {}
        for k, r in lv[child].items():
            pk = k[:cut]
            g = agg.setdefault(pk, {"total": 0, "households": 0,
                                    "age": {b: 0 for b, _, _ in AGE_BUCKETS},
                                    "age_year": {str(a): 0 for a in AGE_DETAIL_YEARS},
                                    "trend12": None})
            g["total"] += r["total"]
            g["households"] += r["households"]
            for b in g["age"]:
                g["age"][b] += r["age"][b]
            for a in g["age_year"]:
                g["age_year"][a] += r["age_year"][a]
            if r["trend12"]:
                if g["trend12"] is None:
                    g["trend12"] = list(r["trend12"])
                elif len(g["trend12"]) == len(r["trend12"]):
                    g["trend12"] = [x + y for x, y in zip(g["trend12"], r["trend12"])]
        for pk, g in agg.items():
            lv[parent][pk] = build_record(g["total"], g["households"], g["age"],
                                          g["trend12"], g["age_year"])

    # 명칭: 팀원1 code_map 이 정본 (표기 차이가 많아 명칭은 절대 조인 키로 쓰지 않는다 — 코드로만 조인)
    if code_map:
        for level, field in (("adm", "adm"), ("sgg", "sgg"), ("sido", "sido")):
            for x in code_map.get(field, []):
                c = str(x["code"])
                if c in lv[level]:
                    names[level][c] = x.get("name", names[level].get(c, ""))

    # 팀원1 code_map 이 있으면 명칭/커버리지 대조
    coverage = {}
    if code_map:
        for level, field in (("adm", "adm"), ("sgg", "sgg"), ("sido", "sido")):
            want = {str(x["code"]) for x in code_map.get(field, [])}
            have = set(lv[level].keys())
            miss, extra = want - have, have - want
            coverage[level] = {
                "code_map": len(want),
                "population": len(have),
                "matched": len(want & have),
                "missing_in_population_cnt": len(miss),
                "missing_in_population_by_sido": dict(sorted(Counter(c[:2] for c in miss).items())),
                "missing_in_population_sample": sorted(miss)[:20],
                "not_in_code_map_cnt": len(extra),
                "not_in_code_map_by_sido": dict(sorted(Counter(c[:2] for c in extra).items())),
                "not_in_code_map_sample": sorted(extra)[:20],
            }
    return lv, names, coverage, conv_stat


def write_outputs(out_dir: Path, lv: dict, meta_base: dict, names: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "adm": out_dir / "population.json",
        "sgg": out_dir / "population_sgg.json",
        "sido": out_dir / "population_sido.json",
    }
    written = {}
    for level, path in files.items():
        meta = dict(meta_base)
        meta["level"] = level
        meta["count"] = len(lv[level])
        doc = {"meta": meta, "data": lv[level], "names": names.get(level, {})}
        # UTF-8 (BOM 없음) + LF 고정
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        written[str(path)] = len(lv[level])
    return written


# ────────────────────────────── --sample ──────────────────────────────
# 인증키/네트워크 없이 전국 규모 더미를 만들기 위한 최소 폴백 테이블.
# (code_map.json 이나 data/raw/population 의 실제 CSV 가 있으면 그쪽 코드를 우선 사용한다)
SIDO_FALLBACK = [
    # (sido_cd, 명칭, 총인구(만명), 시군구 수)
    ("11", "서울특별시", 929, 25), ("26", "부산광역시", 322, 16),
    ("27", "대구광역시", 236, 9),  ("28", "인천광역시", 302, 10),
    ("29", "광주광역시", 140, 5),  ("30", "대전광역시", 143, 5),
    ("31", "울산광역시", 109, 5),  ("36", "세종특별자치시", 39, 1),
    ("41", "경기도", 1365, 42),    ("51", "강원특별자치도", 150, 18),
    ("43", "충청북도", 159, 14),   ("44", "충청남도", 213, 15),
    ("52", "전북특별자치도", 173, 14), ("46", "전라남도", 176, 22),
    ("47", "경상북도", 252, 22),   ("48", "경상남도", 319, 18),
    ("50", "제주특별자치도", 67, 2),
]
# 전국 실제 연령분포(2026 근사) — 더미가 '그럴듯' 하도록 사용
AGE_SHARE = {"0_5": 0.024, "6_11": 0.042, "12_14": 0.025,
             "15_17": 0.026, "18_64": 0.673, "65_": 0.210}


def sample_codes(raw_dir: Path, code_map: dict | None):
    """더미용 코드 목록 확보 우선순위: code_map.json > 기존 raw CSV > 내장 폴백(합성)"""
    if code_map and code_map.get("adm"):
        adm = [(str(x["code"])[:8], x.get("name", ""), str(x.get("parent", ""))[:5])
               for x in code_map["adm"]]
        return adm, "code_map.json"
    for p in sorted(raw_dir.glob("age_*.csv"), reverse=True):
        try:
            m, _ = parse_age_csv(p)
            adm = [(c[:8], short_name(v["name"], "adm"), c[:5])
                   for c, v in m.items() if level_of(c) == "adm"]
            if adm:
                return adm, f"raw/{p.name}"
        except Exception:
            continue
    # 내장 폴백: 시도별 시군구 수 × 시군구당 12~18개 동 합성
    rnd = random.Random(20260723)
    adm = []
    for sido_cd, sname, _pop, n_sgg in SIDO_FALLBACK:
        for g in range(1, n_sgg + 1):
            sgg_cd = f"{sido_cd}{g*10:03d}"
            for d in range(1, rnd.randint(12, 19)):
                adm.append((f"{sgg_cd}{d*10:03d}", f"{sname[:2]}{g}구 {d}동", sgg_cd))
    return adm, "내장 폴백(합성 코드)"


def build_sample(ym: str, raw_dir: Path, code_map: dict | None):
    rnd = random.Random(int(ym))
    adm_list, src = sample_codes(raw_dir, code_map)
    lv = {"adm": {}, "sgg": {}, "sido": {}}
    names = {"adm": {}, "sgg": {}, "sido": {}}

    # 동 단위 인구를 '동 자체의 현실적 분포'로 생성한다.
    #   전국 행정동 ≈ 3,600개 / 총인구 ≈ 5,110만 → 동 평균 ≈ 14,000명 (중앙값 ≈ 12,000)
    #   시도 목표치로 나눠 배분하면 code_map 이 부분 완성일 때 동당 인구가 폭증하므로 쓰지 않는다.
    URBAN = {"11", "26", "27", "28", "29", "30", "31", "36", "41"}  # 특·광역시 + 경기 = 동 규모 큼
    by_sido = {}
    for code8, name, sgg in adm_list:
        by_sido.setdefault(code8[:2], []).append((code8, name, sgg))

    for sido_cd, items in by_sido.items():
        mu = 9.62 if sido_cd in URBAN else 9.05      # exp(9.62)≈15,100 / exp(9.05)≈8,550
        # 비수도권일수록 고령화 높음
        skew = (-0.03 if sido_cd in URBAN else 0.06) + rnd.uniform(-0.02, 0.02)
        for (code8, name, sgg) in items:
            total = max(900, min(90000, int(rnd.lognormvariate(mu, 0.62))))
            share = dict(AGE_SHARE)
            share["65_"] = max(0.08, share["65_"] + skew + rnd.uniform(-0.04, 0.06))
            for k in ("0_5", "6_11", "12_14", "15_17"):
                share[k] = max(0.005, share[k] * rnd.uniform(0.7, 1.35))
            share["18_64"] = max(0.4, 1 - sum(v for k, v in share.items() if k != "18_64"))
            s = sum(share.values())
            age = {k: int(total * v / s) for k, v in share.items()}
            age["18_64"] += total - sum(age.values())
            households = int(total / rnd.uniform(1.9, 2.6))
            drift = rnd.uniform(-0.012, 0.010)
            trend = []
            cur = total * (1 - drift)
            for i in range(12):
                cur *= (1 + drift / 11) * rnd.uniform(0.9985, 1.0015)
                trend.append(int(cur))
            trend[-1] = total
            lv["adm"][code8] = build_record(total, households, age, trend)
            names["adm"][code8] = name

    # 상·하위 집계는 실데이터와 동일 로직 재사용
    for child, parent, cut in (("adm", "sgg", 5), ("sgg", "sido", 2)):
        agg = {}
        for k, r in lv[child].items():
            g = agg.setdefault(k[:cut], {"total": 0, "households": 0,
                                         "age": {b: 0 for b, _, _ in AGE_BUCKETS},
                                         "age_year": {str(a): 0 for a in AGE_DETAIL_YEARS},
                                         "trend12": [0] * 12})
            g["total"] += r["total"]
            g["households"] += r["households"]
            for b in g["age"]:
                g["age"][b] += r["age"][b]
            for a in g["age_year"]:
                g["age_year"][a] += r["age_year"][a]
            g["trend12"] = [x + y for x, y in zip(g["trend12"], r["trend12"])]
        for pk, g in agg.items():
            lv[parent][pk] = build_record(g["total"], g["households"], g["age"],
                                          g["trend12"], g["age_year"])
    for c, n, _p, _g in SIDO_FALLBACK:
        if c in lv["sido"]:
            names["sido"][c] = n
    if code_map:
        for x in code_map.get("sgg", []):
            names["sgg"][str(x["code"])] = x.get("name", "")
        for x in code_map.get("sido", []):
            names["sido"][str(x["code"])] = x.get("name", "")
    return lv, names, src


# ────────────────────────────── main ──────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="행정동별 인구/연령 수집 (DATA_CONTRACT §2-3)")
    ap.add_argument("--sample", action="store_true", help="키 없이 형식 동일한 더미 생성")
    ap.add_argument("--ym", default=None, help="기준 년월 YYYYMM (기본: 전월)")
    ap.add_argument("--out", default=str(OUT_DIR), help="출력 디렉터리")
    ap.add_argument("--raw", default=str(RAW_DIR), help="원본 저장/탐색 디렉터리")
    ap.add_argument("--no-download", action="store_true", help="네트워크 없이 기존 원본만 파싱")
    ap.add_argument("--age-unit", default="1", choices=["1", "5", "10"], help="연령 구분단위")
    args = ap.parse_args()

    ym = args.ym or prev_month_ym()
    out_dir, raw_dir = Path(args.out), Path(args.raw)
    code_map = load_code_map()
    today = date.today().isoformat()

    if args.sample:
        lv, names, src = build_sample(ym, raw_dir, code_map)
        meta = {
            "base_ym": ym,
            "source": "SAMPLE (합성 더미 — 실제 통계 아님)",
            "updated": today,
            "sample": True,
            "code_source": src,
            "age_approx": False,
            "note": "대시보드 선개발용 더미. 규모/연령분포만 한국 실제 통계에 근사시킴.",
        }
        written = write_outputs(out_dir, lv, meta, names)
        print(json.dumps({"mode": "sample", "base_ym": ym, "code_source": src,
                          "written": written}, ensure_ascii=False, indent=1))
        return

    # ── 실데이터 ──
    env = load_env()
    key = env.get("DATA_GO_KR_KEY", "")
    api_hint = fetch_datagokr(ym, key) if key else None
    if key and api_hint is None:
        print("[info] DATA_GO_KR_KEY 는 있으나 API 응답 파싱 실패 → jumin 파일 사용", file=sys.stderr)

    raw_dir.mkdir(parents=True, exist_ok=True)
    age_path, stat_path = raw_dir / f"age_{ym}.csv", raw_dir / f"stat_{ym}.csv"
    dl_log = []

    if not args.no_download:
        for label, path, fn in (("age", age_path, lambda: download_jumin_age(ym, raw_dir, args.age_unit)),
                                ("stat", stat_path, lambda: download_jumin_stat(ym, raw_dir))):
            try:
                p = fn()
                dl_log.append(f"{label}: OK ({p.name}, {p.stat().st_size:,} bytes)")
            except Exception as e:
                dl_log.append(f"{label}: FAIL {e}")
                print(f"[warn] {label} 자동 다운로드 실패: {e}\n"
                      f"       → 파일 상단의 '수동 다운로드 절차' 주석을 따라 "
                      f"{path} 에 넣고 --no-download 로 재실행하세요.", file=sys.stderr)

    if not age_path.exists():
        print(f"[error] {age_path} 없음. 연령별 원본 없이는 진행 불가.", file=sys.stderr)
        sys.exit(2)

    age_map, approx_info = parse_age_csv(age_path)
    stat_map = parse_stat_csv(stat_path) if stat_path.exists() else {}
    if not stat_map:
        print("[warn] stat CSV 없음 → households / trend12 가 비게 됩니다.", file=sys.stderr)

    legacy = load_legacy_map()
    if legacy is None:
        print('[warn] code_legacy_map.json 없음 → 구코드(29/46 등) 변환 없이 진행합니다.', file=sys.stderr)
    lv, names, coverage, conv_stat = assemble(age_map, stat_map, code_map, legacy)

    months = []
    for v in stat_map.values():
        if v.get("months"):
            months = v["months"]
            break

    meta = {
        "base_ym": ym,
        "source": "행안부 주민등록 인구통계 (jumin.mois.go.kr — ageStatMonth.do / statMonth.do)",
        "source_url": [f"{JUMIN}/ageStatMonth.do", f"{JUMIN}/statMonth.do"],
        "updated": today,
        "sample": False,
        "age_unit": f"{approx_info['unit']}세",
        "age_approx": approx_info["approx"],
        "age_approx_note": (
            "원본이 5세/10세 단위라 6_11·12_14·15_17·18_64 경계는 구간 내 균등 배분으로 근사함"
            if approx_info["approx"] else "원본 1세 단위 → 구간 합산(근사 없음)"
        ),
        "age_detail": "0~17세 1세 단위",
        "school_age_range": [SCHOOL_AGE_MIN, SCHOOL_AGE_MAX],
        "trend_months": months,
        "code_map_used": bool(code_map),
        "api_1st_used": bool(api_hint),
        "download_log": dl_log,
        "code_base": "현행(2026-07-01 전남광주통합특별시 12 반영)" if legacy else "원본 구코드(미변환)",
    }
    if coverage:
        meta["coverage_vs_code_map"] = coverage
    if conv_stat:
        meta["legacy_conversion"] = {
            "source": "data/processed/code_legacy_map.json (팀원1)",
            "note": "DATA_CONTRACT §1-1. 출장소 인구는 소속 행정동에 합산됨.",
            **conv_stat,
        }
        # 팀원1이 확증 근거가 없어 비워둔 미매칭 — 추측으로 채우지 않고 조인 실패로 드러나게 둔다
        unmatched = (legacy or {}).get("unmatched", {})
        if unmatched:
            meta["legacy_conversion"]["unmatched_left_asis"] = unmatched

    written = write_outputs(out_dir, lv, meta, names)
    print(json.dumps({"mode": "real", "base_ym": ym, "written": written,
                      "age_approx": approx_info["approx"], "download": dl_log,
                      "conversion": conv_stat}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
