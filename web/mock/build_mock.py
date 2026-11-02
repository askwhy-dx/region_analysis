# -*- coding: utf-8 -*-
"""
build_mock.py — 대시보드 개발용 mock 데이터 생성기 (팀원4 전용)

⚠️ 여기서 만든 파일은 전부 web/mock/ 안에만 쓴다.
   data/geo/, data/processed/ 는 팀원1~3 소유이므로 절대 건드리지 않는다.
   대시보드는 실제 파일(data/…)을 먼저 찾고, 없을 때만 web/mock/ 을 쓴다.

입력: data/raw/geo/HangJeongDong_ver20260701.geojson (행정동 경계 원본)
출력: web/mock/geo/{sido,sgg}.geojson, geo/adm_<sgg>.geojson, geo/adm_index.json
      web/mock/{code_map,population*,apartment*,metrics*}.json
      web/mock/poi_<sgg>.json (collect_poi.py --sample 결과 복사)

실행:
  python web/mock/build_mock.py
"""
from __future__ import annotations

import json
import math
import random
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "raw" / "geo" / "HangJeongDong_ver20260701.geojson"
OUT = ROOT / "web" / "mock"
GEO = OUT / "geo"

# mock 상세(행정동)를 만들 시군구 — collect_poi.py --sample 과 동일 세트
DETAIL_SGG = ["11680", "11650", "41135", "26440"]


# ---------------------------------------------------------------- geometry
def rnd(c, p=5):
    return (round(c[0], p), round(c[1], p))


def rings_of(geom):
    t = geom.get("type")
    if t == "Polygon":
        return list(geom["coordinates"])
    if t == "MultiPolygon":
        out = []
        for poly in geom["coordinates"]:
            out.extend(poly)
        return out
    return []


def dedupe(ring):
    out = []
    for c in ring:
        if not out or out[-1] != c:
            out.append(c)
    return out


def dissolve(feature_geoms, prec=5):
    """공유 변 상쇄(edge cancellation)로 폴리곤 합치기 → 링 리스트."""
    edges = {}
    for g in feature_geoms:
        for ring in rings_of(g):
            r = dedupe([rnd(c, prec) for c in ring])
            if len(r) < 4:
                continue
            if r[0] != r[-1]:
                r.append(r[0])
            for a, b in zip(r[:-1], r[1:]):
                if a == b:
                    continue
                if edges.get((b, a)):
                    edges[(b, a)] -= 1
                    if edges[(b, a)] == 0:
                        del edges[(b, a)]
                else:
                    edges[(a, b)] = edges.get((a, b), 0) + 1
    # 인접 리스트
    adj = {}
    for (a, b), n in edges.items():
        adj.setdefault(a, []).extend([b] * n)
    rings = []
    while adj:
        start = next(iter(adj))
        ring = [start]
        cur = start
        guard = 0
        while True:
            guard += 1
            if guard > 400000:
                break
            nxts = adj.get(cur)
            if not nxts:
                break
            nxt = nxts.pop()
            if not nxts:
                del adj[cur]
            ring.append(nxt)
            cur = nxt
            if cur == start:
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def dp(ring, tol):
    """Douglas-Peucker 단순화 (닫힌 링 유지)."""
    if len(ring) < 5:
        return ring
    def _simp(pts):
        if len(pts) < 3:
            return pts
        a, b = pts[0], pts[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        den = math.hypot(dx, dy) or 1e-12
        idx, dmax = 0, -1
        for i in range(1, len(pts) - 1):
            p = pts[i]
            d = abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / den
            if d > dmax:
                idx, dmax = i, d
        if dmax > tol:
            return _simp(pts[:idx + 1])[:-1] + _simp(pts[idx:])
        return [a, b]
    # 닫힌 링은 시작점 기준 최원점에서 두 조각으로 잘라 각각 DP (a==b 퇴화 방지)
    pts = list(ring[:-1])
    a = pts[0]
    far = max(range(1, len(pts)),
              key=lambda i: (pts[i][0] - a[0]) ** 2 + (pts[i][1] - a[1]) ** 2)
    out = _simp(pts[:far + 1])[:-1] + _simp(pts[far:] + [a])
    if len(out) < 4:
        return ring
    return out


def area(ring):
    s = 0.0
    for a, b in zip(ring[:-1], ring[1:]):
        s += a[0] * b[1] - b[0] * a[1]
    return abs(s) / 2


def to_multipoly(rings, tol, min_area):
    polys = []
    for r in rings:
        if area(r) < min_area:
            continue
        s = dp(r, tol)
        polys.append([[list(c) for c in s]])
    if not polys:
        return None
    return {"type": "MultiPolygon", "coordinates": polys}


# ---------------------------------------------------------------- mock stats
def mk_stats(codes, level, seed=7):
    """계약 스키마에 맞는 population / apartment / metrics 를 결정적으로 생성."""
    pop, apt, met = {}, {}, {}
    for code in codes:
        r = random.Random(int(''.join(ch for ch in code if ch.isdigit()) or 0) + seed)
        scale = {"adm": 1, "sgg": 18, "sido": 260}[level]
        total = r.randint(14000, 42000) * scale
        a05 = int(total * r.uniform(0.028, 0.055))
        a611 = int(total * r.uniform(0.038, 0.072))
        a1214 = int(total * r.uniform(0.018, 0.034))
        a1517 = int(total * r.uniform(0.017, 0.031))
        a65 = int(total * r.uniform(0.11, 0.25))
        a1864 = total - (a05 + a611 + a1214 + a1517 + a65)
        age_year = {}
        for value, lo, hi in ((a05, 0, 5), (a611, 6, 11),
                              (a1214, 12, 14), (a1517, 15, 17)):
            q, rem = divmod(value, hi - lo + 1)
            for offset, age in enumerate(range(lo, hi + 1)):
                age_year[str(age)] = q + (1 if offset < rem else 0)
        school_age = sum(age_year[str(age)] for age in range(3, 17))
        trend_pct = round(r.uniform(-2.4, 3.2), 1)
        base = total - int(total * trend_pct / 100)
        pop[code] = {
            "total": total,
            "households": int(total / r.uniform(2.0, 2.6)),
            "age": {"0_5": a05, "6_11": a611, "12_14": a1214, "15_17": a1517,
                    "18_64": a1864, "65_": a65},
            "age_year": age_year,
            "school_age": school_age,
            "trend12": [int(base + (total - base) * i / 11) for i in range(12)],
            "trend_pct": trend_pct,
        }
        ppy = r.randint(1400, 8200)
        apt[code] = {
            "deal_count": r.randint(20, 260) * (1 if level == "adm" else scale // 4 or 1),
            "avg_price": ppy * r.randint(24, 34),
            "avg_price_per_py": ppy,
            "median_price": int(ppy * r.randint(23, 32)),
            "households": int(pop[code]["households"] * r.uniform(0.45, 0.85)),
            "new_ratio": round(r.uniform(0.03, 0.42), 2),
        }
        academy = max(3, int(school_age / r.uniform(24, 70)))
        sch = max(1, int(school_age / r.uniform(500, 1100)))
        ppa = round(school_age / academy, 1)
        score = max(5.0, min(99.0, round(
            28 + (ppa - 20) * 1.15 + (trend_pct * 3.2) + (ppy / 8200) * 18
            + r.uniform(-6, 6), 1)))
        grade = "S" if score >= 85 else "A" if score >= 70 else "B" if score >= 55 else "C" if score >= 40 else "D"
        met[code] = {
            "school_age": school_age, "academy_cnt": academy,
            "school_cnt": sch, "pupils_per_academy": ppa, "avg_price_per_py": ppy,
            "score": score, "grade": grade,
        }
    return pop, apt, met


def _pip(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
        j = i
    return inside


def relocate_poi(poi: dict, adm_gj: dict, sgg: str):
    """--sample POI 를 실제 행정동 폴리곤 내부 좌표로 재배치 (mock 데모 품질용)."""
    dongs = []
    for ft in adm_gj["features"]:
        code = ft["properties"]["code"]
        rings = [p[0] for p in ft["geometry"]["coordinates"]]
        big = max(rings, key=lambda r: area(r + [r[0]]))
        xs = [c[0] for c in big]; ys = [c[1] for c in big]
        dongs.append((code, big, (min(xs), min(ys), max(xs), max(ys))))
    if not dongs:
        return
    r = random.Random(int(sgg))
    for kind in ("school", "academy", "apartment"):
        for i, p in enumerate(poi.get(kind, [])):
            code, ring, (x0, y0, x1, y1) = dongs[i % len(dongs)]
            for _ in range(300):
                x = r.uniform(x0, x1); y = r.uniform(y0, y1)
                if _pip(x, y, ring):
                    break
            else:
                x, y = (x0 + x1) / 2, (y0 + y1) / 2
            p["lng"], p["lat"] = round(x, 6), round(y, 6)
            p["adm_cd"] = code
    poi.setdefault("meta", {})["note"] = "MOCK — 좌표를 행정동 폴리곤 내부로 재배치함"


def wj(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}  ({path.stat().st_size/1024:.0f} KB)")


def main():
    t0 = time.time()
    if not SRC.exists():
        raise SystemExit(f"원본 경계 파일 없음: {SRC}")
    print("행정동 경계 로딩 중 (약 34MB)…")
    gj = json.loads(SRC.read_text(encoding="utf-8"))
    feats = gj["features"]
    print(f"  features={len(feats)}  ({time.time()-t0:.1f}s)")

    sido_nm, sgg_nm, adm_rows = {}, {}, []
    by_sido, by_sgg = {}, {}
    for ft in feats:
        p = ft["properties"]
        sido, sgg = p["sido"], p["sgg"]
        adm = (p.get("adm_cd2") or "")[:8] or p.get("adm_cd")
        nm = (p.get("adm_nm") or "").split()[-1]
        sido_nm[sido] = p.get("sidonm", "")
        sgg_nm[sgg] = p.get("sggnm", "")
        adm_rows.append({"code": adm, "name": nm, "parent": sgg})
        by_sido.setdefault(sido, []).append(ft["geometry"])
        by_sgg.setdefault(sgg, []).append(ft)

    # ---- code_map
    code_map = {
        "sido": [{"code": c, "name": n} for c, n in sorted(sido_nm.items())],
        "sgg": [{"code": c, "name": sgg_nm[c], "parent": c[:2]} for c in sorted(sgg_nm)],
        "adm": sorted(adm_rows, key=lambda r: r["code"]),
        "bjd2adm": {},
    }
    wj(OUT / "code_map.json", code_map)

    # ---- sido.geojson (dissolve)
    print("시도 경계 dissolve…")
    sido_feats = []
    for cd, geoms in sorted(by_sido.items()):
        rings = dissolve(geoms, 5)
        mp = to_multipoly(rings, 0.005, 1.5e-4)
        if mp:
            sido_feats.append({"type": "Feature",
                               "properties": {"code": cd, "name": sido_nm[cd], "parent": "KR"},
                               "geometry": mp})
    wj(GEO / "sido.geojson", {"type": "FeatureCollection", "features": sido_feats})

    # ---- sgg.geojson (dissolve)
    print("시군구 경계 dissolve…")
    sgg_feats = []
    for cd, fts in sorted(by_sgg.items()):
        rings = dissolve([f["geometry"] for f in fts], 5)
        mp = to_multipoly(rings, 0.002, 3e-5)
        if mp:
            sgg_feats.append({"type": "Feature",
                              "properties": {"code": cd, "name": sgg_nm[cd], "parent": cd[:2]},
                              "geometry": mp})
    wj(GEO / "sgg.geojson", {"type": "FeatureCollection", "features": sgg_feats})

    # ---- adm_<sgg>.geojson (상세 시군구만)
    adm_index = {}
    detail_adm = []
    for sgg in DETAIL_SGG:
        fts = by_sgg.get(sgg)
        if not fts:
            print(f"  [skip] {sgg} 없음")
            continue
        out_f, codes = [], []
        for ft in fts:
            p = ft["properties"]
            adm = (p.get("adm_cd2") or "")[:8]
            rings = dissolve([ft["geometry"]], 5)
            mp = to_multipoly(rings, 0.0002, 1e-7)
            if not mp:
                continue
            out_f.append({"type": "Feature",
                          "properties": {"code": adm,
                                         "name": (p.get("adm_nm") or "").split()[-1],
                                         "parent": sgg},
                          "geometry": mp})
            codes.append(adm)
        wj(GEO / f"adm_{sgg}.geojson", {"type": "FeatureCollection", "features": out_f})
        adm_index[sgg] = codes
        detail_adm.extend(codes)
    wj(GEO / "adm_index.json", adm_index)

    # ---- 통계 mock
    pop_a, apt_a, met_a = mk_stats(detail_adm, "adm")
    pop_s, apt_s, met_s = mk_stats(sorted(sgg_nm), "sgg")
    pop_d, apt_d, met_d = mk_stats(sorted(sido_nm), "sido")
    today = time.strftime("%Y-%m-%d")
    for name, meta, data in [
        ("population.json", {"base_ym": "202606", "source": "MOCK", "updated": today}, pop_a),
        ("population_sgg.json", {"base_ym": "202606", "source": "MOCK", "updated": today}, pop_s),
        ("population_sido.json", {"base_ym": "202606", "source": "MOCK", "updated": today}, pop_d),
        ("apartment.json", {"period": "202507~202606", "source": "MOCK"}, apt_a),
        ("apartment_sgg.json", {"period": "202507~202606", "source": "MOCK"}, apt_s),
        ("apartment_sido.json", {"period": "202507~202606", "source": "MOCK"}, apt_d),
        ("metrics.json", {"updated": today, "level": "adm", "source": "MOCK"}, met_a),
        ("metrics_sgg.json", {"updated": today, "level": "sgg", "source": "MOCK"}, met_s),
        ("metrics_sido.json", {"updated": today, "level": "sido", "source": "MOCK"}, met_d),
    ]:
        wj(OUT / name, {"meta": meta, "data": data})

    # ---- POI: collect_poi.py --sample 산출물을 실제 행정동 폴리곤 안으로 재배치
    for sgg in DETAIL_SGG:
        src = ROOT / "data" / "processed" / f"poi_{sgg}.json"
        if not src.exists():
            print(f"  [skip] poi_{sgg}.json 없음 → collect_poi.py --sample 먼저 실행")
            continue
        poi = json.loads(src.read_text(encoding="utf-8"))
        gfile = GEO / f"adm_{sgg}.geojson"
        if gfile.exists():
            relocate_poi(poi, json.loads(gfile.read_text(encoding="utf-8")), sgg)
        wj(OUT / f"poi_{sgg}.json", poi)

    print(f"완료 ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
