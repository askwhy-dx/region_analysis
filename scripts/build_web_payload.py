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
