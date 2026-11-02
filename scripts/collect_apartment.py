# -*- coding: utf-8 -*-
"""
지역분석 — 아파트 매매 실거래 수집·집계 (담당: 팀원3)

국토교통부_아파트 매매 실거래가 자료 API (공공데이터포털)
  요청 단위: LAWD_CD(시군구 5자리) x DEAL_YMD(YYYYMM)
  전국 250 시군구 x 12개월 = 약 3,000 회 → 재시도/백오프/체크포인트 필수.

산출 (DATA_CONTRACT 2-4):
  data/processed/apartment.json       행정동(adm_cd 8) 단위 — bjd2adm 없으면 법정동(bjd_cd 10) 단위
  data/processed/apartment_sgg.json   시군구
  data/processed/apartment_sido.json  시도
원본:
  data/raw/apartment/<sgg_cd>/<YYYYMM>.json  (API 원응답 아이템 그대로)
  data/raw/apartment/_checkpoint.json        (이어받기)

사용법:
  python collect_apartment.py --sample                       # 키 없이 전국 더미 생성
  python collect_apartment.py --from 202507 --to 202606      # 실수집(기본 최근 12개월)
  python collect_apartment.py --sgg 11680 --sgg 11650        # 특정 시군구만
  python collect_apartment.py --aggregate-only               # 이미 받은 raw 만 재집계

인증키: .env 의 DATA_GO_KR_KEY (하드코딩 금지)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:  # --sample 은 requests 없이도 동작
    requests = None

# ---------------------------------------------------------------- 경로

ROOT = Path(__file__).resolve().parent.parent          # region_analysis/
PROCESSED = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw" / "apartment"
CKPT = RAW / "_checkpoint.json"

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"

PY = 3.305785      # 1평 = 3.3058 m2
SUPPLY_RATIO = 0.75  # 전용률 가정. 평당가는 시장 관행대로 **공급면적** 기준으로 낸다.


def supply_py(area_m2: float) -> float:
    """전용면적(m2) → 공급면적 평수."""
    return area_m2 / SUPPLY_RATIO / PY

# ---------------------------------------------------------------- 지역 참조 테이블
# code_map.json (팀원1 SSOT) 이 없을 때만 쓰는 폴백. 있으면 항상 code_map 우선.

SIDO = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시",
    "29": "광주광역시", "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시",
    "41": "경기도", "43": "충청북도", "44": "충청남도", "46": "전라남도",
    "47": "경상북도", "48": "경상남도", "50": "제주특별자치도",
    "51": "강원특별자치도", "52": "전북특별자치도",
}

SGG = [
    ("11110", "종로구"), ("11140", "중구"), ("11170", "용산구"), ("11200", "성동구"),
    ("11215", "광진구"), ("11230", "동대문구"), ("11260", "중랑구"), ("11290", "성북구"),
    ("11305", "강북구"), ("11320", "도봉구"), ("11350", "노원구"), ("11380", "은평구"),
    ("11410", "서대문구"), ("11440", "마포구"), ("11470", "양천구"), ("11500", "강서구"),
    ("11530", "구로구"), ("11545", "금천구"), ("11560", "영등포구"), ("11590", "동작구"),
    ("11620", "관악구"), ("11650", "서초구"), ("11680", "강남구"), ("11710", "송파구"),
    ("11740", "강동구"),
    ("26110", "중구"), ("26140", "서구"), ("26170", "동구"), ("26200", "영도구"),
    ("26230", "부산진구"), ("26260", "동래구"), ("26290", "남구"), ("26320", "북구"),
    ("26350", "해운대구"), ("26380", "사하구"), ("26410", "금정구"), ("26440", "강서구"),
    ("26470", "연제구"), ("26500", "수영구"), ("26530", "사상구"), ("26710", "기장군"),
    ("27110", "중구"), ("27140", "동구"), ("27170", "서구"), ("27200", "남구"),
    ("27230", "북구"), ("27260", "수성구"), ("27290", "달서구"), ("27710", "달성군"),
    ("27720", "군위군"),
    ("28110", "중구"), ("28140", "동구"), ("28177", "미추홀구"), ("28185", "연수구"),
    ("28200", "남동구"), ("28237", "부평구"), ("28245", "계양구"), ("28260", "서구"),
    ("28710", "강화군"), ("28720", "옹진군"),
    ("29110", "동구"), ("29140", "서구"), ("29155", "남구"), ("29170", "북구"),
    ("29200", "광산구"),
    ("30110", "동구"), ("30140", "중구"), ("30170", "서구"), ("30200", "유성구"),
    ("30230", "대덕구"),
    ("31110", "중구"), ("31140", "남구"), ("31170", "동구"), ("31200", "북구"),
    ("31710", "울주군"),
    ("36110", "세종특별자치시"),
    ("41111", "수원시 장안구"), ("41113", "수원시 권선구"), ("41115", "수원시 팔달구"),
    ("41117", "수원시 영통구"), ("41131", "성남시 수정구"), ("41133", "성남시 중원구"),
    ("41135", "성남시 분당구"), ("41150", "의정부시"), ("41171", "안양시 만안구"),
    ("41173", "안양시 동안구"), ("41190", "부천시"), ("41210", "광명시"),
    ("41220", "평택시"), ("41250", "동두천시"), ("41271", "안산시 상록구"),
    ("41273", "안산시 단원구"), ("41281", "고양시 덕양구"), ("41285", "고양시 일산동구"),
    ("41287", "고양시 일산서구"), ("41290", "과천시"), ("41310", "구리시"),
    ("41360", "남양주시"), ("41370", "오산시"), ("41390", "시흥시"), ("41410", "군포시"),
    ("41430", "의왕시"), ("41450", "하남시"), ("41461", "용인시 처인구"),
    ("41463", "용인시 기흥구"), ("41465", "용인시 수지구"), ("41480", "파주시"),
    ("41500", "이천시"), ("41550", "안성시"), ("41570", "김포시"), ("41590", "화성시"),
    ("41610", "광주시"), ("41630", "양주시"), ("41650", "포천시"), ("41670", "여주시"),
    ("41800", "연천군"), ("41820", "가평군"), ("41830", "양평군"),
    ("43111", "청주시 상당구"), ("43112", "청주시 서원구"), ("43113", "청주시 흥덕구"),
    ("43114", "청주시 청원구"), ("43130", "충주시"), ("43150", "제천시"),
    ("43720", "보은군"), ("43730", "옥천군"), ("43740", "영동군"), ("43745", "증평군"),
    ("43750", "진천군"), ("43760", "괴산군"), ("43770", "음성군"), ("43800", "단양군"),
    ("44131", "천안시 동남구"), ("44133", "천안시 서북구"), ("44150", "공주시"),
    ("44180", "보령시"), ("44200", "아산시"), ("44210", "서산시"), ("44230", "논산시"),
    ("44250", "계룡시"), ("44270", "당진시"), ("44710", "금산군"), ("44760", "부여군"),
    ("44770", "서천군"), ("44790", "청양군"), ("44800", "홍성군"), ("44810", "예산군"),
    ("44825", "태안군"),
    ("46110", "목포시"), ("46130", "여수시"), ("46150", "순천시"), ("46170", "나주시"),
    ("46230", "광양시"), ("46710", "담양군"), ("46720", "곡성군"), ("46730", "구례군"),
    ("46770", "고흥군"), ("46780", "보성군"), ("46790", "화순군"), ("46800", "장흥군"),
    ("46810", "강진군"), ("46820", "해남군"), ("46830", "영암군"), ("46840", "무안군"),
    ("46860", "함평군"), ("46870", "영광군"), ("46880", "장성군"), ("46890", "완도군"),
    ("46900", "진도군"), ("46910", "신안군"),
    ("47111", "포항시 남구"), ("47113", "포항시 북구"), ("47130", "경주시"),
    ("47150", "김천시"), ("47170", "안동시"), ("47190", "구미시"), ("47210", "영주시"),
    ("47230", "영천시"), ("47250", "상주시"), ("47280", "문경시"), ("47290", "경산시"),
    ("47730", "의성군"), ("47750", "청송군"), ("47760", "영양군"), ("47770", "영덕군"),
    ("47820", "청도군"), ("47830", "고령군"), ("47840", "성주군"), ("47850", "칠곡군"),
    ("47900", "예천군"), ("47920", "봉화군"), ("47930", "울진군"), ("47940", "울릉군"),
    ("48121", "창원시 의창구"), ("48123", "창원시 성산구"), ("48125", "창원시 마산합포구"),
    ("48127", "창원시 마산회원구"), ("48129", "창원시 진해구"), ("48170", "진주시"),
    ("48220", "통영시"), ("48240", "사천시"), ("48250", "김해시"), ("48270", "밀양시"),
    ("48310", "거제시"), ("48330", "양산시"), ("48720", "의령군"), ("48730", "함안군"),
    ("48740", "창녕군"), ("48820", "고성군"), ("48840", "남해군"), ("48850", "하동군"),
    ("48860", "산청군"), ("48870", "함양군"), ("48880", "거창군"), ("48890", "합천군"),
    ("50110", "제주시"), ("50130", "서귀포시"),
    ("51110", "춘천시"), ("51130", "원주시"), ("51150", "강릉시"), ("51170", "동해시"),
    ("51190", "태백시"), ("51210", "속초시"), ("51230", "삼척시"), ("51720", "홍천군"),
    ("51730", "횡성군"), ("51750", "영월군"), ("51760", "평창군"), ("51770", "정선군"),
    ("51780", "철원군"), ("51790", "화천군"), ("51800", "양구군"), ("51810", "인제군"),
    ("51820", "고성군"), ("51830", "양양군"),
    ("52111", "전주시 완산구"), ("52113", "전주시 덕진구"), ("52130", "군산시"),
    ("52140", "익산시"), ("52180", "정읍시"), ("52190", "남원시"), ("52210", "김제시"),
    ("52710", "완주군"), ("52720", "진안군"), ("52730", "무주군"), ("52740", "장수군"),
    ("52750", "임실군"), ("52770", "순창군"), ("52790", "고창군"), ("52800", "부안군"),
]

# 시도별 평당가 기준선 (만원/평) — sample 생성용
SIDO_BASE_PY = {
    "11": 3300, "41": 1650, "36": 1450, "28": 1350, "26": 1350, "27": 1250,
    "30": 1300, "29": 1150, "31": 1150, "50": 1200, "43": 900, "44": 900,
    "51": 900, "52": 800, "46": 780, "47": 780, "48": 900,
}

# 시군구 배율 (미지정 = 1.0, '군'은 0.72)
SGG_MULT = {
    "11680": 2.55, "11650": 2.40, "11710": 1.95, "11170": 1.95, "11200": 1.62,
    "11740": 1.42, "11440": 1.45, "11560": 1.38, "11215": 1.40, "11470": 1.32,
    "11590": 1.30, "11110": 1.28, "11140": 1.26, "11290": 1.18, "11410": 1.14,
    "11350": 1.02, "11380": 1.02, "11230": 1.05, "11500": 1.05, "11530": 0.95,
    "11620": 1.00, "11320": 0.90, "11305": 0.88, "11260": 0.92, "11545": 0.90,
    "41135": 2.05, "41290": 1.95, "41465": 1.35, "41117": 1.35, "41463": 1.15,
    "41281": 1.05, "41285": 1.12, "41287": 1.05, "41210": 1.20, "41450": 1.25,
    "41131": 1.30, "41430": 1.10, "41410": 1.05, "41173": 1.20, "41190": 0.95,
    "41220": 0.80, "41250": 0.62, "41650": 0.60, "41800": 0.52, "41820": 0.62,
    "41830": 0.68, "41550": 0.70, "41670": 0.68, "41500": 0.78, "41630": 0.78,
    "27260": 1.55, "26350": 1.45, "26500": 1.40, "30200": 1.35, "29140": 1.20,
    "31140": 1.25, "28185": 1.45, "28260": 1.10, "48123": 1.25, "47190": 0.95,
    "50110": 1.10, "44133": 1.10, "44250": 1.05, "43113": 1.05, "52113": 1.05,
    "51110": 1.00, "51130": 0.95, "46150": 1.00,
}

SGG_NAME = dict(SGG)


def base_price_per_py(sgg_cd: str) -> float:
    base = SIDO_BASE_PY.get(sgg_cd[:2], 1000)
    mult = SGG_MULT.get(sgg_cd)
    if mult is None:
        mult = 0.72 if SGG_NAME.get(sgg_cd, "").endswith("군") else 1.0
    return base * mult


# ---------------------------------------------------------------- 유틸

def log(msg: str) -> None:
    print(msg, flush=True)


def load_env_key() -> str | None:
    """.env 에서 DATA_GO_KR_KEY 를 읽는다. 하드코딩 금지."""
    if os.environ.get("DATA_GO_KR_KEY"):
        return os.environ["DATA_GO_KR_KEY"]
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("DATA_GO_KR_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  wrote {path.relative_to(ROOT)}  ({path.stat().st_size/1024:.0f} KB)")


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # 깨진 파일은 없는 셈 친다
        log(f"  ! {path.name} 읽기 실패: {e}")
        return None


def month_range(ym_from: str, ym_to: str) -> list[str]:
    y, m = int(ym_from[:4]), int(ym_from[4:6])
    out = []
    while f"{y:04d}{m:02d}" <= ym_to:
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
        if len(out) > 600:
            break
    return out


def default_period() -> tuple[str, str]:
    """오늘 기준 직전 12개월 (당월 제외 — 신고지연 때문에 당월은 표본이 얇다)."""
    t = date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    to = f"{y:04d}{m:02d}"
    y2, m2 = y, m - 11
    while m2 <= 0:
        m2 += 12
        y2 -= 1
    return f"{y2:04d}{m2:02d}", to


# ---------------------------------------------------------------- 코드맵

def load_code_map() -> dict:
    """팀원1의 code_map.json. 없으면 폴백 테이블로 최소 구조를 만든다."""
    cm = read_json(PROCESSED / "code_map.json")
    if cm and cm.get("sgg"):
        return {
            "sido": {x["code"]: x["name"] for x in cm.get("sido", [])},
            "sgg": {x["code"]: x["name"] for x in cm.get("sgg", [])},
            "adm": {x["code"]: x for x in cm.get("adm", [])},
            "bjd2adm": cm.get("bjd2adm", {}) or {},
            "source": "code_map.json",
        }
    return {
        "sido": dict(SIDO),
        "sgg": dict(SGG),
        "adm": {},
        "bjd2adm": {},
        "source": "fallback(collect_apartment 내장표)",
    }


def target_sgg_list(cm: dict, only: list[str] | None) -> list[str]:
    """
    실수집 대상 시군구. code_map 이 아직 일부만 채워져 있을 수 있으므로
    (code_map ∪ population.json ∪ 내장 폴백표) 의 합집합을 쓴다.
    """
    codes = set(cm["sgg"].keys()) | set(SGG_NAME.keys())
    pop = read_json(PROCESSED / "population.json")
    if pop and isinstance(pop.get("data"), dict):
        codes |= {k[:5] for k in pop["data"] if len(k) >= 5}
    codes = sorted(codes)
    if only:
        codes = [c for c in codes if c in set(only)]
    return codes


def adm_universe(cm: dict) -> tuple[dict[str, list[str]], str]:
    """
    시군구별 행정동코드 목록. 우선순위: population.json > code_map.adm > 없음.
    sample 생성 시 다른 팀원 산출물과 **키가 반드시 맞도록** 하기 위한 것.
    """
    pop = read_json(PROCESSED / "population.json")
    if pop and isinstance(pop.get("data"), dict) and pop["data"]:
        by: dict[str, list[str]] = {}
        for k in pop["data"]:
            if len(k) == 8:
                by.setdefault(k[:5], []).append(k)
        if by:
            return {k: sorted(v) for k, v in by.items()}, "population.json"
    if cm.get("adm"):
        by = {}
        for code, rec in cm["adm"].items():
            by.setdefault(rec.get("parent") or code[:5], []).append(code)
        if by:
            return {k: sorted(v) for k, v in by.items()}, "code_map.json(adm)"
    return {}, ""


# ---------------------------------------------------------------- API 수집

def _text(el, tag: str, default: str = "") -> str:
    n = el.find(tag)
    return (n.text or default).strip() if n is not None and n.text else default


def parse_items(xml_text: str) -> tuple[list[dict], int]:
    """실거래가 API XML → item dict 목록, totalCount."""
    root = ET.fromstring(xml_text)
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "00"
    if code not in ("00", "0000", "000"):
        msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or "unknown"
        raise RuntimeError(f"API error {code}: {msg}")
    total = int(root.findtext(".//totalCount") or 0)
    items = []
    for it in root.findall(".//item"):
        items.append({t.tag: (t.text or "").strip() for t in it})
    return items, total


def fetch_month(key: str, sgg_cd: str, ym: str, max_retry: int = 5) -> list[dict]:
    """한 시군구·한 달치 전량(페이지 순회). 재시도 + 지수 백오프."""
    if requests is None:
        raise RuntimeError("requests 미설치 — 실수집 불가")
    out: list[dict] = []
    page, rows = 1, 1000
    while True:
        params = {
            "serviceKey": key, "LAWD_CD": sgg_cd, "DEAL_YMD": ym,
            "pageNo": page, "numOfRows": rows,
        }
        delay = 1.0
        for attempt in range(1, max_retry + 1):
            try:
                r = requests.get(API_URL, params=params, timeout=30)
                r.raise_for_status()
                items, total = parse_items(r.text)
                break
            except Exception as e:
                if attempt == max_retry:
                    raise RuntimeError(f"{sgg_cd}/{ym} p{page} 실패: {e}") from e
                log(f"    retry {attempt}/{max_retry} ({sgg_cd}/{ym} p{page}): {e}")
                time.sleep(delay)
                delay = min(delay * 2, 30)
        out.extend(items)
        if page * rows >= total or not items:
            break
        page += 1
        time.sleep(0.15)
    return out


def collect(key: str, sgg_codes: list[str], months: list[str], sleep: float) -> None:
    """raw 로 내려받기. (sgg, ym) 단위 체크포인트 — 중단 후 이어받기 가능."""
    RAW.mkdir(parents=True, exist_ok=True)
    ckpt = read_json(CKPT) or {"done": [], "failed": {}}
    done = set(ckpt.get("done", []))
    failed = dict(ckpt.get("failed", {}))
    todo = [(s, m) for s in sgg_codes for m in months if f"{s}:{m}" not in done]
    log(f"수집 대상 {len(todo)} 건 (완료 {len(done)} 건 건너뜀)")

    for i, (s, m) in enumerate(todo, 1):
        tag = f"{s}:{m}"
        try:
            items = fetch_month(key, s, m)
            write_json(RAW / s / f"{m}.json", items)
            done.add(tag)
            failed.pop(tag, None)
        except Exception as e:
            failed[tag] = str(e)[:300]
            log(f"  X {tag}: {e}")
        if i % 20 == 0 or i == len(todo):
            write_json(CKPT, {"done": sorted(done), "failed": failed,
                              "updated": date.today().isoformat()})
            log(f"  진행 {i}/{len(todo)}  실패 {len(failed)}")
        time.sleep(sleep)
    write_json(CKPT, {"done": sorted(done), "failed": failed,
                      "updated": date.today().isoformat()})
    if failed:
        log(f"실패 {len(failed)} 건 — 같은 명령 재실행하면 실패분만 다시 시도합니다.")


# ---------------------------------------------------------------- 거래 정규화

def norm_deal(it: dict, sgg_cd: str) -> dict | None:
    """API item → 표준 거래 레코드. 파싱 실패 건은 None."""
    def g(*names):
        for n in names:
            if it.get(n):
                return it[n].strip()
        return ""
    try:
        amount = int(g("dealAmount", "거래금액").replace(",", ""))          # 만원
        area = float(g("excluUseAr", "전용면적"))                            # m2
    except Exception:
        return None
    if amount <= 0 or area <= 0:
        return None
    bjd = g("sggCd", "지역코드") + g("umdCd", "법정동시군구코드")
    bjd_cd = (bjd + "00")[:10] if len(bjd) >= 8 else ""
    if not bjd_cd.startswith(sgg_cd):
        bjd_cd = sgg_cd + (g("umdCd") or "000") .zfill(5)
        bjd_cd = bjd_cd[:10].ljust(10, "0")
    try:
        built = int(g("buildYear", "건축년도") or 0)
    except Exception:
        built = 0
    return {
        "name": g("aptNm", "aptName", "아파트") or "미상",
        "bjd_cd": bjd_cd,
        "dong": g("umdNm", "법정동"),
        "amount": amount,
        "area_m2": area,
        "area_py": area / PY,
        "built": built,
        "ym": (g("dealYear", "년") or "") + (g("dealMonth", "월") or "").zfill(2),
    }


def load_raw_deals(sgg_codes: list[str], months: set[str]) -> dict[str, list[dict]]:
    """raw/apartment 에서 거래를 읽어 시군구별로 모은다."""
    out: dict[str, list[dict]] = {}
    for s in sgg_codes:
        d = RAW / s
        if not d.is_dir():
            continue
        deals = []
        for f in sorted(d.glob("*.json")):
            if months and f.stem not in months:
                continue
            for it in (read_json(f) or []):
                n = norm_deal(it, s)
                if n:
                    deals.append(n)
        if deals:
            out[s] = deals
    return out


# ---------------------------------------------------------------- sample 생성

def sample_deals(sgg_codes: list[str], months: list[str], cm: dict,
                 adm_by_sgg: dict[str, list[str]], seed: int = 20260723
                 ) -> dict[str, list[dict]]:
    """
    전국 규모의 그럴듯한 더미 거래를 만든다.
    지역 편차(강남 > 지방 군) · 동별 편차 · 단지별 편차 · 면적/연식 분포 반영.

    행정동 유니버스(population.json 등)가 있으면 **그 행정동코드 위에** 생성한다.
    → metrics 조립 시 인구·POI 와 키가 100% 맞는다. 없으면 합성 법정동코드로 생성.
    """
    rnd = random.Random(seed)
    deals: dict[str, list[dict]] = {}

    for s in sgg_codes:
        name = cm["sgg"].get(s, SGG_NAME.get(s, s))
        is_gun = name.endswith("군")
        base = base_price_per_py(s)

        adm_list = adm_by_sgg.get(s) or []
        if adm_list:
            units = [(a, None) for a in adm_list]
        else:
            # 동 개수: 대도시 구 11~19, 시 8~15, 군 5~9
            if is_gun:
                n_dong = rnd.randint(5, 9)
            elif s[:2] in ("11", "26", "27", "28", "29", "30", "31"):
                n_dong = rnd.randint(11, 19)
            else:
                n_dong = rnd.randint(8, 15)
            # 법정동코드 10자리 = 시군구(5) + 읍면동(3) + 리(2)
            units = [(None, f"{s}{101 + i:03d}00") for i in range(n_dong)]

        lst: list[dict] = []

        for di, (adm_cd, bjd) in enumerate(units):
            dong_mult = rnd.uniform(0.78, 1.30)          # 동별 편차
            short = name.split()[-1]
            dong_name = f"{short}{di+1}동" if not is_gun else f"{short}{di+1}리"
            key_cd = adm_cd or bjd
            # 아파트가 아예 없는 동(전원·산지)도 현실적으로 존재
            if rnd.random() < (0.30 if is_gun else 0.06):
                continue
            n_apt = rnd.randint(2, 4) if is_gun else rnd.randint(4, 11)
            for ai in range(n_apt):
                apt_mult = rnd.uniform(0.82, 1.28)
                built = rnd.choices(
                    [rnd.randint(2018, 2025), rnd.randint(2006, 2017), rnd.randint(1985, 2005)],
                    weights=[0.22, 0.40, 0.38])[0]
                age_adj = 1.16 if built >= 2018 else (1.0 if built >= 2006 else 0.86)
                ppy = base * dong_mult * apt_mult * age_adj
                apt_name = f"{dong_name[:-1]}{rnd.choice(['e편한세상','자이','푸르지오','래미안','힐스테이트','아이파크','더샵','한신','우성','현대','주공','한라비발디','호반베르디움'])}{ai+1}단지"
                households = rnd.choice([180, 240, 320, 420, 540, 680, 860, 1120, 1450])
                # 거래건수: 비싼 곳일수록 회전율 낮고, 세대수 클수록 많다
                base_cnt = households / 55 * rnd.uniform(0.5, 1.6)
                n_deal = max(1, int(base_cnt * (0.65 if ppy > 4000 else 1.0)))
                n_deal = min(n_deal, 40)
                for _ in range(n_deal):
                    area = rnd.choices([39.6, 49.5, 59.9, 74.8, 84.9, 99.7, 114.9, 134.8],
                                       weights=[.05, .08, .24, .10, .33, .10, .07, .03])[0]
                    py = supply_py(area)          # 공급면적 평수
                    # 소형일수록 평당가 높음
                    size_adj = 1.14 if area < 50 else (1.06 if area < 62 else (1.0 if area < 90 else 0.95))
                    amt = ppy * py * size_adj * rnd.uniform(0.93, 1.08)
                    rec = {
                        "name": apt_name,
                        "bjd_cd": bjd or (key_cd + "00"),
                        "dong": dong_name,
                        "amount": int(round(amt / 100) * 100),
                        "area_m2": area,
                        "area_py": py,
                        "built": built,
                        "households": households,
                        "ym": rnd.choice(months),
                    }
                    if adm_cd:
                        rec["adm_cd"] = adm_cd
                    lst.append(rec)
        deals[s] = lst
    return deals


# ---------------------------------------------------------------- 집계

def _stats(deals: list[dict]) -> dict:
    amts = [d["amount"] for d in deals]
    pys = [d["amount"] / supply_py(d["area_m2"]) for d in deals if d["area_m2"] > 0]
    this_year = date.today().year
    new_cnt = sum(1 for d in deals if d.get("built") and this_year - d["built"] <= 10)
    hh = sum({(d["name"], d["bjd_cd"]): d.get("households", 0)
              for d in deals}.values()) or None
    return {
        "deal_count": len(deals),
        "avg_price": int(round(statistics.fmean(amts))),
        "avg_price_per_py": int(round(statistics.fmean(pys))) if pys else None,
        "median_price": int(round(statistics.median(amts))),
        "households": int(hh) if hh else None,
        "new_ratio": round(new_cnt / len(deals), 3) if deals else None,
    }


def build_complex_index(all_deals: dict[str, list[dict]]) -> dict:
    """
    좌표가 없는 실거래를 팀원4 POI(아파트)와 조인하기 위한 키 인덱스.
    key   = "<bjd_cd 10자리>|<단지명>"
    value = [deal_count, avg_price, avg_price_per_py, built, households]  (용량 때문에 배열)
    팀원4는 poi_<sgg>.json 의 apartment[].name + (bjd/adm) 로 매칭한다.
    """
    acc: dict[str, list] = {}
    for deals in all_deals.values():
        for d in deals:
            k = f"{d.get('adm_cd') or d['bjd_cd']}|{d['name']}"
            e = acc.get(k)
            if e is None:
                e = acc[k] = [0, 0.0, 0.0, d.get("built") or None, d.get("households")]
            e[0] += 1
            e[1] += d["amount"]
            e[2] += d["amount"] / supply_py(d["area_m2"]) if d["area_m2"] else 0
    return {k: [e[0], int(round(e[1] / e[0])), int(round(e[2] / e[0])), e[3], e[4]]
            for k, e in acc.items()}


def _merge_into(path: Path, new_data: dict, new_meta: dict,
                sgg_scope: set[str] | None, extra: dict | None = None) -> dict:
    """
    부분 수집(--sgg)일 때 기존 전국 결과를 덮어쓰지 않고 해당 시군구만 교체한다.
    sgg_scope 가 None(전체 수집)이면 그대로 통째로 쓴다.
    """
    if not sgg_scope:
        out = {"meta": new_meta, "data": new_data}
        if extra:
            out.update(extra)
        return out
    old = read_json(path) or {}
    lvl = new_meta.get("level")
    data = dict(old.get("data") or {})
    for k in list(data):
        if lvl == "sido":
            if k in {s[:2] for s in sgg_scope}:
                data.pop(k)
        elif lvl == "sgg":
            if k in sgg_scope:
                data.pop(k)
        else:
            if k[:5] in sgg_scope:
                data.pop(k)
    data.update(new_data)
    meta = dict(old.get("meta") or {})
    meta.update(new_meta)
    meta["region_count"] = len(data)
    meta["partial_update"] = sorted(sgg_scope)
    if lvl == "sido":
        meta["partial_warning"] = ("부분 수집이라 이 시도 값은 수집된 시군구만의 합계일 수 있음")
    out = {"meta": meta, "data": data}
    if extra:
        for k, v in extra.items():
            base = dict((old.get(k) or {}))
            base.update(v)
            out[k] = base
    return out


def aggregate(all_deals: dict[str, list[dict]], cm: dict, period: str,
              sample: bool, note: str, sgg_scope: set[str] | None = None) -> None:
    bjd2adm: dict = cm.get("bjd2adm") or {}
    has_adm = any(d.get("adm_cd") for v in all_deals.values() for d in v[:1])
    level = "adm" if (bjd2adm or has_adm) else "bjd"
    unmapped = 0

    # --- 최소 단위 (행정동 또는 법정동)
    bucket: dict[str, list[dict]] = {}
    for s, deals in all_deals.items():
        for d in deals:
            if d.get("adm_cd"):                       # sample: 이미 행정동 단위
                bucket.setdefault(d["adm_cd"], []).append(d)
                continue
            targets = bjd2adm.get(d["bjd_cd"])
            if targets:
                for a in targets:
                    bucket.setdefault(a, []).append(d)
            else:
                if bjd2adm:
                    unmapped += 1
                bucket.setdefault(d["bjd_cd"], []).append(d)

    data = {k: _stats(v) for k, v in sorted(bucket.items())}
    sgg_data = {s: _stats(v) for s, v in sorted(all_deals.items()) if v}
    sido_bucket: dict[str, list[dict]] = {}
    for s, v in all_deals.items():
        sido_bucket.setdefault(s[:2], []).extend(v)
    sido_data = {k: _stats(v) for k, v in sorted(sido_bucket.items()) if v}

    meta_common = {
        "period": period,
        "source": "국토교통부_아파트 매매 실거래가 자료 (data.go.kr)",
        "updated": date.today().isoformat(),
        "unit": "만원",
        "code_map_source": cm.get("source"),
        "note": note,
        "dashboard_hint": "코로플레스는 metrics.json 을 쓰세요. 이 파일은 원지표/조인용입니다.",
    }
    if sample:
        meta_common["sample"] = True

    m_adm = dict(meta_common, level=level, region_count=len(data),
                 complex_index_key=("<adm_cd>|<단지명>" if has_adm else "<bjd_cd>|<단지명>"),
                 complex_index_schema=["deal_count", "avg_price", "avg_price_per_py",
                                       "built", "households"])
    if level == "bjd":
        m_adm["bjd2adm_missing"] = True
        m_adm["level_note"] = ("code_map.json 의 bjd2adm 가 없어 **법정동(10자리) 단위**로 산출함. "
                               "code_map.json 확보 후 --aggregate-only 재실행하면 행정동 단위로 재생성됨.")
    elif has_adm:
        m_adm["level_note"] = ("sample 은 행정동 유니버스(population.json) 위에 직접 생성되어 "
                               "bjd2adm 변환이 필요 없음.")
    else:
        m_adm["bjd_unmapped_deals"] = unmapped
        m_adm["split_rule"] = ("법정동이 여러 행정동에 걸치면(N:M) 해당 법정동 거래를 "
                               "각 행정동에 **중복 배정**한다(평균가 왜곡 방지). "
                               "따라서 행정동 deal_count 합계 > 시군구 deal_count 일 수 있음.")

    write_json(PROCESSED / "apartment.json",
               _merge_into(PROCESSED / "apartment.json", data, m_adm, sgg_scope,
                           {"complex_index": build_complex_index(all_deals)}))
    write_json(PROCESSED / "apartment_sgg.json",
               _merge_into(PROCESSED / "apartment_sgg.json", sgg_data,
                           dict(meta_common, level="sgg", region_count=len(sgg_data)),
                           sgg_scope))
    write_json(PROCESSED / "apartment_sido.json",
               _merge_into(PROCESSED / "apartment_sido.json", sido_data,
                           dict(meta_common, level="sido", region_count=len(sido_data)),
                           sgg_scope))
    log(f"집계 완료: {level} {len(data)} · sgg {len(sgg_data)} · sido {len(sido_data)}")


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="아파트 실거래 수집·집계 (팀원3)")
    ap.add_argument("--sample", action="store_true", help="API 키 없이 더미 데이터 생성")
    ap.add_argument("--from", dest="ym_from", help="시작 YYYYMM")
    ap.add_argument("--to", dest="ym_to", help="종료 YYYYMM")
    ap.add_argument("--sgg", action="append", help="특정 시군구코드(반복 지정 가능)")
    ap.add_argument("--aggregate-only", action="store_true", help="raw 재집계만")
    ap.add_argument("--sleep", type=float, default=0.25, help="요청 간 대기(초)")
    a = ap.parse_args(argv)

    d_from, d_to = default_period()
    ym_from = a.ym_from or d_from
    ym_to = a.ym_to or d_to
    months = month_range(ym_from, ym_to)
    period = f"{ym_from}~{ym_to}"

    cm = load_code_map()
    sgg_codes = target_sgg_list(cm, a.sgg)
    # --sgg 부분 수집이면 기존 전국 결과를 덮어쓰지 않고 해당 시군구만 교체한다
    scope = set(a.sgg) if a.sgg else None
    if scope:
        log(f"부분 모드: {sorted(scope)} 만 갱신하고 나머지 지역은 기존 값을 유지합니다.")
    admu, admu_src = adm_universe(cm)
    log(f"기간 {period} ({len(months)}개월) · 시군구 {len(sgg_codes)}개 · 코드맵={cm['source']}")
    if admu:
        log(f"행정동 유니버스: {sum(len(v) for v in admu.values())}개 (출처 {admu_src})")

    if a.sample:
        if admu:
            # 유령 키 방지: sample 은 인구/경계가 존재하는 시군구에만 생성한다.
            keep = set(admu) & set(sgg_codes)
            dropped = len(sgg_codes) - len(keep)
            sgg_codes = sorted(keep)
            if dropped:
                log(f"[sample] 행정동 유니버스에 없는 시군구 {dropped}개 제외 → {len(sgg_codes)}개")
        log("[sample] 더미 실거래 생성 중...")
        deals = sample_deals(sgg_codes, months, cm, admu)
        n = sum(len(v) for v in deals.values())
        log(f"[sample] 거래 {n:,} 건 생성")
        aggregate(deals, cm, period, True,
                  "SAMPLE — 실제 실거래가 아님. 대시보드 개발용 더미.", scope)
        return 0

    if not cm.get("bjd2adm"):
        log("! code_map.json 의 bjd2adm 없음 → 실수집분은 법정동 단위로 산출됩니다 (meta 에 기록).")

    if not a.aggregate_only:
        key = load_env_key()
        if not key:
            log("! .env 에 DATA_GO_KR_KEY 가 없습니다. --sample 로 더미를 쓰거나 키를 발급하세요.")
            return 2
        collect(key, sgg_codes, months, a.sleep)

    deals = load_raw_deals(sgg_codes, set(months))
    if not deals:
        log("! raw 에 거래가 없습니다. 수집 먼저 하세요.")
        return 3
    log(f"raw 거래 {sum(len(v) for v in deals.values()):,} 건 로드")
    aggregate(deals, cm, period, False, "국토부 실거래 원자료 집계", scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
