# -*- coding: utf-8 -*-
"""
collect_poi.py — 시군구 단위 POI 수집기 (담당: 팀원4)

산출: data/processed/poi_<sgg_cd>.json   (스키마는 docs/DATA_CONTRACT.md §2-5)

수집 대상
  school     전국초중등학교위치표준데이터 (좌표 포함, WGS84)
  academy    지방행정 인허가 - 학원교습소업   (좌표 EPSG:5174 계열 -> 변환)
  apartment  K-APT 공동주택 단지 목록/기본정보 (좌표 없으면 주소기반 스킵)

사용법
  # 키 없이 형식 동일한 더미 생성 (대시보드 개발용)
  python scripts/collect_poi.py --sample --sgg 11680
  python scripts/collect_poi.py --sample                # 기본 샘플 시군구 세트

  # 실제 수집 (.env 의 DATA_GO_KR_KEY 필요)
  python scripts/collect_poi.py --sgg 11680
  python scripts/collect_poi.py --sgg 11680,11650 --kind academy,school

주의
  * LOCALDATA(localdata.go.kr)는 2026-04-16 종료. 옛 API 코드 사용 금지.
    본 스크립트는 공공데이터포털(data.go.kr) 이관 엔드포인트만 사용한다.
  * geopandas/pyproj 없이 동작하도록 좌표변환을 순수 python으로 구현했다.
    (pyproj 가 설치돼 있으면 자동으로 그쪽을 사용 — 정확도 검증용)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "processed"
RAW_DIR = ROOT / "data" / "raw" / "poi"

# --------------------------------------------------------------------------
# 0. .env
# --------------------------------------------------------------------------

def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("DATA_GO_KR_KEY",):
        if k in os.environ and os.environ[k]:
            env[k] = os.environ[k]
    return env


# --------------------------------------------------------------------------
# 1. 좌표계 변환  (EPSG:5174 / 5179 / 5186 -> WGS84)  — 순수 python
# --------------------------------------------------------------------------
# 인허가(구 LOCALDATA 계열) 좌표는 대부분 EPSG:5174 (Bessel 1841, 중부원점 TM,
# false easting 200000 / northing 500000, k0=1).  일부는 5179(UTM-K/GRS80).
# 변환 절차: TM 역투영 -> (Bessel) 측지좌표 -> ECEF -> Bursa-Wolf 3파라미터
#            평행이동 -> (WGS84) 측지좌표.

_ELLIPSOIDS = {
    "bessel": (6377397.155, 1 / 299.1528128),
    "grs80": (6378137.0, 1 / 298.257222101),
    "wgs84": (6378137.0, 1 / 298.257223563),
}

# name: (ellipsoid, lat0, lon0, k0, false_e, false_n, towgs84(dx,dy,dz))
CRS_DEFS = {
    "EPSG:5174": ("bessel", 38.0, 127.0028902777778, 1.0, 200000.0, 500000.0,
                  (-145.907, 505.034, 685.756)),
    "EPSG:5173": ("bessel", 38.0, 125.0028902777778, 1.0, 200000.0, 500000.0,
                  (-145.907, 505.034, 685.756)),
    "EPSG:5175": ("bessel", 38.0, 127.0028902777778, 1.0, 200000.0, 550000.0,
                  (-145.907, 505.034, 685.756)),
    "EPSG:5176": ("bessel", 38.0, 129.0028902777778, 1.0, 200000.0, 500000.0,
                  (-145.907, 505.034, 685.756)),
    "EPSG:5186": ("grs80", 38.0, 127.0, 1.0, 200000.0, 600000.0, (0.0, 0.0, 0.0)),
    "EPSG:5179": ("grs80", 38.0, 127.5, 0.9996, 1000000.0, 2000000.0, (0.0, 0.0, 0.0)),
}

try:  # 선택적 — 있으면 검증/우선 사용
    from pyproj import Transformer as _PyprojTransformer  # type: ignore
    HAS_PYPROJ = True
except Exception:  # pragma: no cover
    _PyprojTransformer = None
    HAS_PYPROJ = False


def _tm_inverse(x: float, y: float, a: float, f: float, lat0: float, lon0: float,
                k0: float, fe: float, fn: float):
    """Transverse Mercator 역투영 -> (lat, lon) in degrees (해당 타원체 기준)."""
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lat0r = math.radians(lat0)
    lon0r = math.radians(lon0)

    def _M(phi):
        return a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
                    - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
                    + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
                    - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))

    M0 = _M(lat0r)
    M = M0 + (y - fn) / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    C1 = ep2 * cos1 ** 2
    T1 = tan1 ** 2
    N1 = a / math.sqrt(1 - e2 * sin1 ** 2)
    R1 = a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5
    D = (x - fe) / (N1 * k0)

    lat = phi1 - (N1 * tan1 / R1) * (
        D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = lon0r + (
        D - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120) / cos1
    return math.degrees(lat), math.degrees(lon)


def _geodetic_to_ecef(lat, lon, a, f, h=0.0):
    e2 = 2 * f - f * f
    latr, lonr = math.radians(lat), math.radians(lon)
    N = a / math.sqrt(1 - e2 * math.sin(latr) ** 2)
    X = (N + h) * math.cos(latr) * math.cos(lonr)
    Y = (N + h) * math.cos(latr) * math.sin(lonr)
    Z = (N * (1 - e2) + h) * math.sin(latr)
    return X, Y, Z


def _ecef_to_geodetic(X, Y, Z, a, f):
    e2 = 2 * f - f * f
    b = a * (1 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(X, Y)
    th = math.atan2(a * Z, b * p)
    lon = math.atan2(Y, X)
    lat = math.atan2(Z + ep2 * b * math.sin(th) ** 3,
                     p - e2 * a * math.cos(th) ** 3)
    return math.degrees(lat), math.degrees(lon)


class CrsConverter:
    """<crs> 평면좌표 -> WGS84 경위도. pyproj 있으면 pyproj 사용."""

    def __init__(self, crs: str = "EPSG:5174"):
        self.crs = crs
        if crs not in CRS_DEFS:
            raise ValueError(f"지원하지 않는 좌표계: {crs}")
        self._pp = None
        if HAS_PYPROJ:
            try:
                self._pp = _PyprojTransformer.from_crs(crs, "EPSG:4326", always_xy=True)
            except Exception:
                self._pp = None

    def to_wgs84(self, x: float, y: float):
        """returns (lng, lat) — GeoJSON 순서."""
        if self._pp is not None:
            lng, lat = self._pp.transform(x, y)
            return lng, lat
        ell, lat0, lon0, k0, fe, fn, tow = CRS_DEFS[self.crs]
        a, f = _ELLIPSOIDS[ell]
        lat, lon = _tm_inverse(x, y, a, f, lat0, lon0, k0, fe, fn)
        if tow != (0.0, 0.0, 0.0):
            X, Y, Z = _geodetic_to_ecef(lat, lon, a, f)
            X, Y, Z = X + tow[0], Y + tow[1], Z + tow[2]
            wa, wf = _ELLIPSOIDS["wgs84"]
            lat, lon = _ecef_to_geodetic(X, Y, Z, wa, wf)
        return lon, lat


def guess_crs(x: float, y: float) -> str:
    """좌표 크기로 좌표계 추정 (인허가 데이터는 컬럼에 좌표계 표기가 없는 경우가 많다)."""
    if 120.0 < x < 132.0 and 32.0 < y < 40.0:
        return "EPSG:4326"
    if x > 600000 and y > 1000000:
        return "EPSG:5179"
    if 100000 < x < 400000 and 400000 < y < 800000:
        return "EPSG:5174"
    return "EPSG:5174"


KOREA_BBOX = (124.0, 32.0, 132.5, 39.5)  # lng_min, lat_min, lng_max, lat_max


def in_korea(lng: float, lat: float) -> bool:
    return (KOREA_BBOX[0] <= lng <= KOREA_BBOX[2]) and (KOREA_BBOX[1] <= lat <= KOREA_BBOX[3])


# --------------------------------------------------------------------------
# 2. 공공데이터포털 호출 유틸
# --------------------------------------------------------------------------

API = {
    # 전국초중등학교위치표준데이터 (표준데이터 공통 엔드포인트)
    "school": "http://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api",
    # 지방행정 인허가 데이터 (LOCALDATA 이관분) — 업종코드로 구분
    "license": "http://api.odcloud.kr/api/15130096/v1/uddi:license",   # ※ 운영 전 확인 필요
    # K-APT 공동주택 단지 목록
    "apt_list": "http://apis.data.go.kr/1613000/AptListService2/getLegaldongAptList",
    "apt_info": "http://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusBassInfoV3",
}
LICENSE_BIZ = {"academy": "학원교습소업"}

OPEN_STATUS_OK = ("영업", "정상", "영업/정상", "01")


def http_get_json(url: str, params: dict, retry: int = 3, timeout: int = 20):
    import requests
    last = None
    for i in range(retry):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            txt = r.text.strip()
            if txt.startswith("<"):
                raise RuntimeError(f"XML/HTML 응답 (키·엔드포인트 확인): {txt[:200]}")
            return json.loads(txt)
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"API 호출 실패: {url} :: {last}")


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", " "):
            return d[k]
        for dk in d:
            if dk.lower() == k.lower() and d[dk] not in (None, "", " "):
                return d[dk]
    return default


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


# --------------------------------------------------------------------------
# 3. 실제 수집기
# --------------------------------------------------------------------------

def fetch_school(key: str, sgg_cd: str, dropped: dict) -> list:
    """전국초중등학교위치표준데이터. 응답 좌표는 WGS84(위도/경도) 컬럼 제공."""
    rows, page = [], 1
    while True:
        js = http_get_json(API["school"], {
            "serviceKey": key, "pageNo": page, "numOfRows": 1000,
            "type": "json", "ADRES": "",
        })
        items = (js.get("response", {}).get("body", {}) or {}).get("items") or []
        if isinstance(items, dict):
            items = items.get("item", [])
        if not items:
            break
        rows.extend(items)
        if len(items) < 1000:
            break
        page += 1
        if page > 60:
            break

    out = []
    for it in rows:
        code = str(_first(it, "sigunguCd", "ctprvnCd", default="") or "")
        addr = str(_first(it, "rdnmadr", "lnmadr", "ADRES", default="") or "")
        if code and not code.startswith(sgg_cd):
            if sgg_cd not in addr:
                continue
        lat = _num(_first(it, "latitude", "la", "LAT"))
        lng = _num(_first(it, "longitude", "lo", "LOT"))
        name = _first(it, "schoolNm", "SCHUL_NM", "fondSchoolNm", default="")
        kind = str(_first(it, "schoolSe", "schoolKindScNm", default="") or "")
        if lat is None or lng is None or not in_korea(lng, lat):
            dropped["school"] = dropped.get("school", 0) + 1
            continue
        out.append({
            "name": name,
            "type": "초등" if "초" in kind else ("중등" if "중" in kind else kind or "기타"),
            "lat": round(lat, 6), "lng": round(lng, 6),
            "adm_cd": None,
            "students": int(_num(_first(it, "stdntCo", default=0)) or 0),
        })
    return out


def fetch_license(key: str, sgg_cd: str, biz: str, dropped: dict, kind: str) -> list:
    """지방행정 인허가 (학원교습소업).

    ⚠️ 인허가 통합 API 는 data.go.kr 이관 후 데이터셋별 uddi 경로가 다르다.
    운영 전 실제 승인받은 데이터셋의 엔드포인트로 API['license'] 를 교체할 것.
    """
    rows, page = [], 1
    while True:
        js = http_get_json(API["license"], {
            "serviceKey": key, "page": page, "perPage": 1000,
            "cond[SIGUN_CD::EQ]": sgg_cd, "opnSvcNm": biz,
        })
        items = js.get("data") or js.get("items") or []
        if not items:
            break
        rows.extend(items)
        if len(items) < 1000:
            break
        page += 1
        if page > 60:
            break

    conv_cache = {}
    out = []
    for it in rows:
        status = str(_first(it, "trdStateNm", "영업상태명", "dtlStateNm", default="") or "")
        end = str(_first(it, "dcbYmd", "폐업일자", default="") or "").strip()
        if end or not any(s in status for s in OPEN_STATUS_OK):
            dropped[kind + "_closed"] = dropped.get(kind + "_closed", 0) + 1
            continue
        x = _num(_first(it, "x", "좌표정보(x)", "xCnts", "coordX"))
        y = _num(_first(it, "y", "좌표정보(y)", "yDnts", "coordY"))
        if x is None or y is None:
            dropped[kind] = dropped.get(kind, 0) + 1
            continue
        crs = guess_crs(x, y)
        if crs == "EPSG:4326":
            lng, lat = x, y
        else:
            if crs not in conv_cache:
                conv_cache[crs] = CrsConverter(crs)
            lng, lat = conv_cache[crs].to_wgs84(x, y)
        if not in_korea(lng, lat):
            dropped[kind] = dropped.get(kind, 0) + 1
            continue
        name = _first(it, "bplcNm", "사업장명", default="")
        rec = {"name": name, "lat": round(lat, 6), "lng": round(lng, 6), "adm_cd": None}
        if kind == "academy":
            rec["course"] = str(_first(it, "crsTypeNm", "교습과정명", default="기타") or "기타")
        out.append(rec)
    return out


def fetch_apartment(key: str, sgg_cd: str, dropped: dict) -> list:
    """K-APT 단지목록 + 기본정보. 단지목록에는 좌표가 없어 기본정보로 보강한다."""
    out = []
    js = http_get_json(API["apt_list"], {
        "serviceKey": key, "sigunguCode": sgg_cd, "pageNo": 1, "numOfRows": 1000,
        "_type": "json",
    })
    items = ((js.get("response", {}).get("body", {}) or {}).get("items") or {})
    if isinstance(items, dict):
        items = items.get("item", [])
    for it in items or []:
        code = _first(it, "kaptCode")
        name = _first(it, "kaptName", default="")
        if not code:
            continue
        try:
            info = http_get_json(API["apt_info"], {
                "serviceKey": key, "kaptCode": code, "_type": "json"})
            d = ((info.get("response", {}).get("body", {}) or {}).get("item") or {})
        except Exception:
            d = {}
        lat = _num(_first(d, "latitude", "coordinateY"))
        lng = _num(_first(d, "longitude", "coordinateX"))
        if lat is None or lng is None or not in_korea(lng, lat):
            dropped["apartment"] = dropped.get("apartment", 0) + 1
            continue
        built = str(_first(d, "kaptUsedate", default="") or "")[:4]
        out.append({
            "name": name, "lat": round(lat, 6), "lng": round(lng, 6), "adm_cd": None,
            "households": int(_num(_first(d, "kaptdaCnt", default=0)) or 0),
            "avg_price": None,
            "built": int(built) if built.isdigit() else None,
        })
        time.sleep(0.05)
    return out


# --------------------------------------------------------------------------
# 4. 행정동 매칭 (code_map / 경계 geojson 이 있으면 point-in-polygon)
# --------------------------------------------------------------------------

def load_adm_polys(sgg_cd: str):
    f = ROOT / "data" / "geo" / f"adm_{sgg_cd}.geojson"
    if not f.exists():
        return None
    try:
        gj = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    polys = []
    for ft in gj.get("features", []):
        code = (ft.get("properties") or {}).get("code")
        geom = ft.get("geometry") or {}
        rings = []
        if geom.get("type") == "Polygon":
            rings = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            rings = geom["coordinates"]
        for poly in rings:
            polys.append((code, poly))
    return polys


def _pip(lng, lat, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            if lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
        j = i
    return inside


def match_adm(polys, lng, lat):
    if not polys:
        return None
    for code, poly in polys:
        if not poly:
            continue
        if _pip(lng, lat, poly[0]):
            if any(_pip(lng, lat, h) for h in poly[1:]):
                continue
            return code
    return None


# --------------------------------------------------------------------------
# 5. 샘플(더미) 생성 — 키 없이 대시보드 개발용, 스키마 동일
# --------------------------------------------------------------------------

SAMPLE_SGG = {
    "11680": ("강남구", 127.047, 37.497),
    "11650": ("서초구", 127.007, 37.483),
    "41135": ("성남시 분당구", 127.121, 37.382),
    "26440": ("부산 강서구", 128.947, 35.163),
}
COURSES = ["보통교과", "외국어", "예능", "속셈", "논술", "컴퓨터"]


def build_sample(sgg_cd: str, seed: int = 0) -> dict:
    name, clng, clat = SAMPLE_SGG.get(sgg_cd, (f"시군구{sgg_cd}", 127.0, 37.5))
    rnd = random.Random(int(sgg_cd) + seed)
    adms = [f"{sgg_cd}{str(i).zfill(3)}" for i in (510, 520, 530, 540, 550, 560)]

    def scatter(i):
        return (round(clng + rnd.uniform(-0.035, 0.035), 6),
                round(clat + rnd.uniform(-0.028, 0.028), 6))

    school, academy, apartment = [], [], []
    for i in range(rnd.randint(9, 16)):
        lng, lat = scatter(i)
        t = "초등" if i % 3 != 2 else "중등"
        school.append({"name": f"{name[:2]}{'제' if i else ''}{i+1}{'초등학교' if t=='초등' else '중학교'}",
                       "type": t, "lat": lat, "lng": lng,
                       "adm_cd": adms[i % len(adms)],
                       "students": rnd.randint(220, 1180)})
    for i in range(rnd.randint(120, 260)):
        lng, lat = scatter(i)
        academy.append({"name": f"{rnd.choice(['한빛','미래','으뜸','에이스','청담','파란','수학의숲','리드'])}"
                                f"{rnd.choice(['영어','수학','국어','논술','과학','미술'])}학원 {i+1}호",
                        "course": rnd.choice(COURSES), "lat": lat, "lng": lng,
                        "adm_cd": adms[i % len(adms)]})
    for i in range(rnd.randint(20, 55)):
        lng, lat = scatter(i)
        apartment.append({"name": f"{rnd.choice(['래미안','자이','푸르지오','e편한세상','힐스테이트'])} "
                                  f"{name[:2]}{i+1}단지",
                          "lat": lat, "lng": lng, "adm_cd": adms[i % len(adms)],
                          "households": rnd.randint(120, 2400),
                          "avg_price": rnd.randint(38000, 320000),
                          "built": rnd.randint(1988, 2025)})
    return {
        "meta": {
            "sgg_cd": sgg_cd, "sgg_nm": name, "mode": "sample",
            "source": "SAMPLE(더미) — 실데이터 아님",
            "updated": time.strftime("%Y-%m-%d"),
            "dropped": {"school": 0, "academy": 0, "apartment": 0},
            "counts": {"school": len(school), "academy": len(academy),
                       "apartment": len(apartment)},
        },
        "school": school, "academy": academy, "apartment": apartment,
    }


def place_in_adm(obj: dict, polys, sgg_cd: str):
    """--sample 좌표를 실제 행정동 폴리곤 내부로 재배치하고 adm_cd 를 일치시킨다."""
    dongs = []
    for code, poly in polys:
        ring = poly[0]
        xs = [c[0] for c in ring]; ys = [c[1] for c in ring]
        dongs.append((code, ring, (min(xs), min(ys), max(xs), max(ys))))
    if not dongs:
        return
    # 같은 행정동이 MultiPolygon 으로 여러 조각이면 가장 큰 조각만 사용
    best = {}
    for code, ring, bb in dongs:
        sz = (bb[2] - bb[0]) * (bb[3] - bb[1])
        if code not in best or sz > best[code][2]:
            best[code] = (ring, bb, sz)
    items = list(best.items())
    rnd = random.Random(int(sgg_cd))
    for kind in ("school", "academy", "apartment"):
        for i, p in enumerate(obj.get(kind, [])):
            code, (ring, bb, _) = items[i % len(items)]
            x = y = None
            for _ in range(400):
                cx = rnd.uniform(bb[0], bb[2]); cy = rnd.uniform(bb[1], bb[3])
                if _pip(cx, cy, ring):
                    x, y = cx, cy
                    break
            if x is None:
                x, y = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            p["lng"], p["lat"], p["adm_cd"] = round(x, 6), round(y, 6), code


# --------------------------------------------------------------------------
# 6. main
# --------------------------------------------------------------------------

def write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="POI 수집 (학교/학원/아파트)")
    ap.add_argument("--sample", action="store_true", help="API 키 없이 더미 데이터 생성")
    ap.add_argument("--sgg", default="", help="시군구코드 5자리, 콤마 구분 (예: 11680,11650)")
    ap.add_argument("--kind", default="school,academy,apartment",
                    help="수집 대상 (콤마 구분)")
    ap.add_argument("--out", default=str(OUT_DIR), help="출력 디렉터리")
    ap.add_argument("--selftest", action="store_true", help="좌표변환 자체검증만 수행")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    out_dir = Path(args.out)
    sggs = [s.strip() for s in args.sgg.split(",") if s.strip()]

    if args.sample:
        if not sggs:
            sggs = list(SAMPLE_SGG.keys())
        for s in sggs:
            obj = build_sample(s)
            # 실제 행정동 경계가 있으면 더미 좌표를 그 안으로 재배치 (adm_cd 정합)
            polys = load_adm_polys(s)
            if polys:
                place_in_adm(obj, polys, s)
                obj["meta"]["source"] += " / 좌표는 data/geo 경계 내부로 배치"
            p = out_dir / f"poi_{s}.json"
            write_json(p, obj)
            print(f"[sample] {p}  " + ", ".join(
                f"{k}={v}" for k, v in obj["meta"]["counts"].items()))
        return 0

    env = load_env()
    key = env.get("DATA_GO_KR_KEY")
    if not key:
        print("[ERROR] .env 의 DATA_GO_KR_KEY 가 없습니다. 키 없이 개발하려면 --sample 을 쓰세요.",
              file=sys.stderr)
        return 2
    if not sggs:
        print("[ERROR] --sgg 로 시군구코드를 지정하세요.", file=sys.stderr)
        return 2

    kinds = [k.strip() for k in args.kind.split(",") if k.strip()]
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for s in sggs:
        dropped: dict = {}
        res = {"school": [], "academy": [], "apartment": []}
        for k in kinds:
            try:
                if k == "school":
                    res["school"] = fetch_school(key, s, dropped)
                elif k == "academy":
                    res[k] = fetch_license(key, s, LICENSE_BIZ[k], dropped, k)
                elif k == "apartment":
                    res["apartment"] = fetch_apartment(key, s, dropped)
            except Exception as e:
                print(f"[WARN] {s} {k} 수집 실패: {e}", file=sys.stderr)
        # 원본 백업
        write_json(RAW_DIR / f"raw_{s}.json", res)

        polys = load_adm_polys(s)
        if polys:
            for k, arr in res.items():
                for r in arr:
                    r["adm_cd"] = match_adm(polys, r["lng"], r["lat"])
        else:
            print(f"[WARN] data/geo/adm_{s}.geojson 없음 → adm_cd 미매칭(null)", file=sys.stderr)

        obj = {"meta": {
            "sgg_cd": s, "mode": "api", "source": "data.go.kr (인허가/표준데이터/K-APT)",
            "updated": time.strftime("%Y-%m-%d"), "dropped": dropped,
            "counts": {k: len(v) for k, v in res.items()},
        }}
        obj.update(res)
        p = out_dir / f"poi_{s}.json"
        write_json(p, obj)
        print(f"[api] {p}  " + ", ".join(f"{k}={len(v)}" for k, v in res.items())
              + f"  dropped={dropped}")
    return 0


def selftest():
    """알려진 기준점으로 좌표변환 검증."""
    conv = CrsConverter("EPSG:5174")
    cases = [
        # (x, y, 기대 lng, 기대 lat, 설명)
        (200000.0, 500000.0, 127.00289, 38.0 - 0.0, "중부원점 (Bessel→WGS84 시프트 반영)"),
        (201000.0, 445000.0, None, None, "서울 근방"),
    ]
    ok = True
    for x, y, elng, elat, note in cases:
        lng, lat = conv.to_wgs84(x, y)
        print(f"  ({x},{y}) -> lng={lng:.6f} lat={lat:.6f}   {note}")
        if not in_korea(lng, lat):
            ok = False
            print("   [FAIL] 한반도 범위 밖")
    print("pyproj:", "사용" if HAS_PYPROJ else "미설치(순수 python 경로)")
    print("selftest:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
