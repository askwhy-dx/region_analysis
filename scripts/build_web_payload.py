# -*- coding: utf-8 -*-
"""Build small, route-scoped payloads for the static dashboard.

The canonical processed and GeoJSON files remain unchanged. The dashboard uses
these derived files so opening one region does not download nationwide data.
"""
from __future__ import annotations

import json
from pathlib import Path

from geo_build import dumps, simplify_multipolygon


ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "data" / "geo"
PROC = ROOT / "data" / "processed"
SIDO_TOL = 2e-5
SGG_TOL = 1.5e-6


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> int:
    text = dumps(value)
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def simplify_geo(geo, tolerance: float):
    features = []
    for feature in geo["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        features.append({
            "type": "Feature",
            "properties": feature["properties"],
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": simplify_multipolygon(polygons, tolerance),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def subset(document, codes):
    wanted = set(codes)
    return {
        "meta": document.get("meta", {}),
        "data": {code: value for code, value in document.get("data", {}).items() if code in wanted},
    }


def bundle(metrics, population, competitor, codes, geo=None):
    value = {
        "meta": {"schema": "web-bundle-v1"},
        "metrics": subset(metrics, codes),
        "population": subset(population, codes),
        "competitor": subset(competitor, codes),
    }
    if geo is not None:
        value["geo"] = geo
    return value


# 화면 「데이터 갱신 주기」 팝업이 읽는다 (2026-11-03). 최종 수정일 = 원천 산출물 meta.updated 중 가장 늦은 날.
UPDATE_SOURCES = [
    ("인구", "population.json"), ("아파트 실거래", "apartment.json"), ("아파트 세대수", "kapt.json"),
    ("어린이집 원아", "childcare.json"), ("학원", "academy.json"), ("학교", "school.json"),
    ("자사·경쟁사·시너지세권", "competitor_adm.json"),
]


def update_info():
    items, dates = [], []
    for name, fname in UPDATE_SOURCES:
        meta = read(PROC / fname).get("meta", {})
        basis = meta.get("period") or meta.get("base_ym") or (
            f"{meta['base_year']}학년도" if meta.get("base_year") else meta.get("collected_at") or "")
        if meta.get("updated"):
            dates.append(meta["updated"])
        items.append({"name": name, "basis": basis, "updated": meta.get("updated", "")})
    return {"meta": {"schema": "web-update-info-v1"}, "updated": max(dates) if dates else "", "items": items}


def main():
    sido_geo = simplify_geo(read(GEO / "sido.geojson"), SIDO_TOL)
    sgg_geo = simplify_geo(read(GEO / "sgg.geojson"), SGG_TOL)

    metrics_sido = read(PROC / "metrics_sido.json")
    population_sido = read(PROC / "population_sido.json")
    competitor_sido = read(PROC / "competitor_sido.json")
    sido_codes = sorted(metrics_sido["data"])

    outputs = {}
    outputs["web_sido.json"] = write(
        PROC / "web_sido.json",
        bundle(metrics_sido, population_sido, competitor_sido, sido_codes, sido_geo),
    )

    metrics_sgg = read(PROC / "metrics_sgg.json")
    population_sgg = read(PROC / "population_sgg.json")
    competitor_sgg = read(PROC / "competitor_sgg.json")
    for sido_code in sido_codes:
        codes = sorted(code for code in metrics_sgg["data"] if code.startswith(sido_code))
        geo = {
            "type": "FeatureCollection",
            "features": [
                feature for feature in sgg_geo["features"]
                if str(feature["properties"].get("parent", feature["properties"]["code"]))[:2] == sido_code
            ],
        }
        name = f"web_sgg_{sido_code}.json"
        outputs[name] = write(PROC / name, bundle(metrics_sgg, population_sgg, competitor_sgg, codes, geo))

    metrics_adm = read(PROC / "metrics.json")
    population_adm = read(PROC / "population.json")
    competitor_adm = read(PROC / "competitor_adm.json")
    for geo_path in sorted(GEO.glob("adm_*.geojson")):
        sgg_code = geo_path.stem.removeprefix("adm_")
        codes = sorted(code for code in metrics_adm["data"] if code.startswith(sgg_code))
        name = f"web_adm_{sgg_code}.json"
        outputs[name] = write(PROC / name, bundle(metrics_adm, population_adm, competitor_adm, codes))

    outputs["web_update_info.json"] = write(PROC / "web_update_info.json", update_info())

    result = {
        "status": "OK",
        "files": len(outputs),
        "bytes": sum(outputs.values()),
        "largest": sorted(outputs.items(), key=lambda item: item[1], reverse=True)[:5],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
