# -*- coding: utf-8 -*-
"""
admlookup.py — 좌표 → 행정동(adm_cd) 판정 공용 모듈

DATA_CONTRACT §1 / docs/ADDR_TO_ADM.md 의 결론을 그대로 구현한다.

  * **좌표 PIP 가 정본이다.** 법정동코드(bjd_cd)·행정동명은 조인 키로 쓰지 않는다.
      - 법정동:행정동은 N:M 이라 매핑표가 원리적으로 부정확하다 (bjd2adm 경로 2026-07-29 폐기)
      - 동 명칭은 표기 차이가 많다 (`창신제1동` = `창신1동` 류 358건)
  * 구코드(광주 29 / 전남 46 등)는 `code_legacy_map.json` 으로 변환한 뒤 쓴다.
    법정동코드는 **힌트로만** 쓰고(어느 시군구 경계를 먼저 볼지), 최종 판정은 좌표가 한다.

사용
    from admlookup import AdmIndex
    idx = AdmIndex.load()
    adm = idx.lookup(127.03, 37.50)                 # 전국 탐색
    adm = idx.lookup(127.03, 37.50, sgg_hint="11680")   # 힌트 있으면 훨씬 빠르다
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEO_DIR = ROOT / "data" / "geo"
LEGACY = ROOT / "data" / "processed" / "code_legacy_map.json"


# --------------------------------------------------------------------------
# 구↔신 코드 변환
# --------------------------------------------------------------------------

class LegacyMap:
    def __init__(self, d: dict):
        self.sido = d.get("sido", {})
        self.sgg = d.get("sgg", {})
        self.adm = d.get("adm", {})
        self.branch = d.get("branch_office", {})

    @classmethod
    def load(cls) -> "LegacyMap":
        if not LEGACY.exists():
            return cls({})
        return cls(json.loads(LEGACY.read_text(encoding="utf-8")))

    def fix_sgg(self, cd: str) -> str:
        cd = (cd or "").strip()
        return self.sgg.get(cd, cd)

    def fix_adm(self, cd: str) -> str:
        cd = (cd or "").strip()
        cd = self.adm.get(cd, cd)
        return self.branch.get(cd, cd)


# --------------------------------------------------------------------------
# 좌표 → 행정동
# --------------------------------------------------------------------------

def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


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


class AdmIndex:
    """전국 행정동 폴리곤 인덱스 (bbox 선필터 + PIP)."""

    def __init__(self, polys: list, by_sgg: dict):
        self.polys = polys          # [(adm_cd, sgg_cd, bbox, poly)]
        self.by_sgg = by_sgg        # {sgg_cd: [idx, ...]}

    @classmethod
    def load(cls, verbose: bool = False) -> "AdmIndex":
        polys, by_sgg = [], {}
        files = sorted(GEO_DIR.glob("adm_*.geojson"))
        if not files:
            raise RuntimeError(f"경계 파일이 없다: {GEO_DIR}  (scripts/geo_build.py 먼저 실행)")
        for f in files:
            sgg_cd = f.stem.split("_", 1)[1]
            gj = json.loads(f.read_text(encoding="utf-8"))
            for ft in gj.get("features", []):
                code = (ft.get("properties") or {}).get("code")
                geom = ft.get("geometry") or {}
                if geom.get("type") == "Polygon":
                    rings = [geom["coordinates"]]
                elif geom.get("type") == "MultiPolygon":
                    rings = geom["coordinates"]
                else:
                    continue
                for poly in rings:
                    if not poly or not poly[0]:
                        continue
                    by_sgg.setdefault(sgg_cd, []).append(len(polys))
                    polys.append((code, sgg_cd, _ring_bbox(poly[0]), poly))
        if verbose:
            print(f"[admlookup] 경계 {len(files)}개 파일 · 폴리곤 {len(polys):,}개 "
                  f"· 시군구 {len(by_sgg)}개")
        return cls(polys, by_sgg)

    def _hit(self, i, lng, lat):
        code, _sgg, bb, poly = self.polys[i]
        if not (bb[0] <= lng <= bb[2] and bb[1] <= lat <= bb[3]):
            return None
        if not _pip(lng, lat, poly[0]):
            return None
        if any(_pip(lng, lat, h) for h in poly[1:]):   # 구멍(내부 링)이면 제외
            return None
        return code

    def lookup(self, lng: float, lat: float, sgg_hint: str | None = None):
        """좌표 → adm_cd. 판정 실패 시 None.

        sgg_hint 는 **속도용 힌트일 뿐**이다 — 힌트가 틀려도(구코드·오기재)
        전국 탐색으로 폴백하므로 결과는 좌표가 결정한다.
        """
        if lng is None or lat is None:
            return None
        if sgg_hint:
            for i in self.by_sgg.get(sgg_hint, ()):
                c = self._hit(i, lng, lat)
                if c:
                    return c
        for i in range(len(self.polys)):
            c = self._hit(i, lng, lat)
            if c:
                return c
        return None

    def sgg_of(self, adm_cd: str) -> str:
        return (adm_cd or "")[:5]
