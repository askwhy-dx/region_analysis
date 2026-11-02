# -*- coding: utf-8 -*-
"""diagnose_apt_ratio.py — 아파트 거주비율 100% 초과 동 원인 분류 — **읽기 전용**

    python scripts/diagnose_apt_ratio.py
    python scripts/diagnose_apt_ratio.py --all      초과가 아닌 동까지 전부 (참고용)

왜 필요한가
-----------
`apt_ratio > 1`(아파트 세대수 > 주민등록 세대수)은 물리적으로 불가능해 보이지만,
**원인이 하나가 아니다.** 지금은 전부 `apt_ratio_suspect` 하나로 뭉뚱그려 경고하는데,
그중 상당수는 **정상**이라 진짜 문제가 묻힌다.

  · 미입주형 — 신축 대단지는 준공 후에도 전입이 늦다. 아파트는 다 지어졌는데
               주민등록이 아직 안 옮겨온 상태다. **시간이 해결한다.**
               (실측: 아라2동 신축비중 100%·준공 3.7년차 1.127)
  · 경계형   — 경계 폴리곤이 낡아 옆 동 아파트가 이 동에 얹혔다. 그래서 **옆 동은
               반대로 비어 있다**(부흥동 1.39 ↔ 비산3동 0.42). 원천이 갱신돼야 풀린다.
  · 불명     — 위 어느 쪽으로도 설명 안 되는 것. 개별 확인 대상이다.

⚠️ **이 스크립트는 아무것도 고치지 않는다.** 분류만 해서 근거와 함께 내놓는다.
   실측(2026-08-12): 안양 부흥동은 좌표 PIP·통계청·행안부가 **전부 부흥동**으로 일치했고
   K-apt 중복 등록도 없었다. 즉 **심판을 다시 돌려도 안 바뀌는** 종류가 섞여 있다.

인접 판정
---------
경계 폴리곤의 **꼭짓점을 공유**하면 인접으로 본다(같은 원천에서 디졸브돼 좌표가 일치한다).
공유점이 없으면 중심점 거리로 보완한다 — 간소화로 꼭짓점이 어긋난 경우가 있어서다.

산출
----
  data/logs/apt_ratio_diagnosis_YYYYMMDD.json
  data/logs/apt_ratio_diagnosis_YYYYMMDD.csv   (엑셀에서 바로 열리게 BOM 포함)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"
G = ROOT / "data" / "geo"
LOGS = ROOT / "data" / "logs"

# ── 분류 기준 (실측 근거는 위 주석) ─────────────────────────────────────────
NEW_HIGH = 0.5      # 신축(10년 이내) 세대 비중이 이 이상이면 미입주 의심
AGE_YOUNG = 9.0     # 세대 가중 평균 준공연차가 이 미만이면 미입주 의심
NEIGHBOR_LOW = 0.65  # 인접 동 비율이 이 미만이면 '비어 있다' = 밀고 당기는 쌍
# ⚠️ 처음엔 경계형에 '신축비중 20% 미만'까지 요구했는데 **너무 좁았다**(실측):
#    양학동(인접 0.01)·잠실4동(0.03)·법1동(0.08) 처럼 옆 동이 명백히 빈 곳들이
#    신축이 조금 섞였다는 이유로 전부 '불명'에 떨어졌다.
#    **밀고 당기는 쌍은 신축으로는 생기지 않는다** — 인접 신호가 신축 여부보다 강한 증거다.
#    그래서 미입주형이 아닌 것 중 인접이 비었으면 경계형으로 본다.


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_shapes() -> tuple[dict, dict]:
    """행정동별 (꼭짓점 집합, 중심점). 좌표는 소수 5자리로 반올림해 비교한다."""
    pts: dict[str, set] = {}
    cent: dict[str, tuple] = {}
    for f in sorted(G.glob("adm_*.geojson")):
        for ft in json.loads(f.read_text(encoding="utf-8"))["features"]:
            code = ft["properties"]["code"]
            s = set()
            geom = ft.get("geometry") or {}
            polys = geom.get("coordinates") or []
            if geom.get("type") == "Polygon":
                polys = [polys]
            for poly in polys:
                for ring in poly:
                    for x, y in ring:
                        s.add((round(x, 5), round(y, 5)))
            pts[code] = s
            if s:
                cent[code] = (sum(x for x, _ in s) / len(s), sum(y for _, y in s) / len(s))
    return pts, cent


def neighbors(codes: list[str], pts: dict, cent: dict) -> dict[str, list[str]]:
    """같은 시군구 안에서만 본다 — 인접 동이 다른 시군구인 경우는 경계 문제가 아니라
    시군구 경계라 이 진단의 대상이 아니다."""
    by_sgg: dict[str, list[str]] = defaultdict(list)
    for c in codes:
        by_sgg[c[:5]].append(c)
    out: dict[str, list[str]] = {}
    for sgg, group in by_sgg.items():
        for c in group:
            adj = [o for o in group if o != c and pts.get(c) and pts.get(o)
                   and len(pts[c] & pts[o]) >= 2]
            if not adj and c in cent:                 # 꼭짓점이 안 맞으면 거리로 보완
                d = sorted(((math.dist(cent[c], cent[o]), o) for o in group
                            if o != c and o in cent))
                adj = [o for _, o in d[:3]]
            out[c] = adj
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="아파트 거주비율 초과 동 원인 분류 (읽기 전용)")
    ap.add_argument("--all", action="store_true", help="초과가 아닌 동까지 전부 출력")
    a = ap.parse_args(argv)

    met = read(P / "metrics.json")["data"]
    kapt = (read(P / "kapt.json") or {}).get("data") or {}
    sgg_met = read(P / "metrics_sgg.json")["data"]
    sido_met = read(P / "metrics_sido.json")["data"]
    gc = (read(P / "geocode_cache.json") or {}).get("data") or {}

    # 심판 이력 — 이 동에 배정된 주소 중 심판이 손댄 것이 있나
    arb_by_dong: dict[str, int] = defaultdict(int)
    for v in gc.values():
        if v and v.get("arbiter") in ("juso", "pip_outlier") and v.get("adm_cd"):
            arb_by_dong[v["adm_cd"]] += 1

    codes = [c for c, v in met.items() if v.get("apt_households") and v.get("households")]
    pts, cent = load_shapes()
    adj = neighbors(codes, pts, cent)

    def ratio(c):
        v = met.get(c) or {}
        h, a_ = v.get("households"), v.get("apt_households")
        return (a_ / h) if (h and a_) else None

    target = [c for c in codes if (met[c].get("apt_ratio") or 0) > 1.0] if not a.all else codes
    rows = []
    for c in sorted(target, key=lambda x: -(met[x].get("apt_ratio") or 0)):
        v = met[c]
        r = v.get("apt_ratio")
        new_r, age = v.get("new_ratio"), v.get("built_age")
        pop, hh, ah = v.get("population") or 0, v.get("households") or 0, v.get("apt_households") or 0
        per = (pop / hh) if hh else None
        # 인접 동 중 가장 비어 있는 곳
        lows = sorted(((ratio(o), o) for o in adj.get(c, []) if ratio(o) is not None))
        low_r, low_c = (lows[0] if lows else (None, None))

        newbuild = (new_r is not None and new_r >= NEW_HIGH) or (age is not None and age < AGE_YOUNG)
        boundary = (not newbuild and low_r is not None and low_r < NEIGHBOR_LOW)
        kind = "미입주형" if newbuild else ("경계형" if boundary else "불명")
        why = ("신축비중 %.0f%%·준공 %.1f년차" % ((new_r or 0) * 100, age or 0)) if newbuild else (
              ("인접 %s 비율 %.2f 로 비어 있음" % (met.get(low_c, {}).get("name"), low_r))
              if boundary else "신축·인접 어느 쪽으로도 설명 안 됨")

        rows.append({
            "adm_cd": c, "시도": sido_met.get(c[:2], {}).get("name", ""),
            "시군구": sgg_met.get(c[:5], {}).get("name", ""), "동": v.get("name"),
            "판정": kind, "근거": why,
            "거주비율": r, "아파트세대": ah, "주민등록세대": hh, "초과세대": ah - hh,
            "인구": pop, "세대당인원": round(per, 2) if per else None,
            "신축비중": new_r, "준공연차": age,
            "인접최저비율": round(low_r, 3) if low_r is not None else None,
            "인접동": met.get(low_c, {}).get("name") if low_c else None,
            "임대비중": (round((kapt.get(c, {}).get("rental_hh") or 0)
                             / (kapt.get(c, {}).get("households") or 1), 3)),
            "심판교정건수": arb_by_dong.get(c, 0),
        })

    tally = defaultdict(int)
    for r in rows:
        tally[r["판정"]] += 1
    print(f"[진단] 거주비율 100% 초과 {len(rows)}개 동")
    for k in ("미입주형", "경계형", "불명"):
        print(f"   {k:<6} {tally[k]:>3}개")
    print()
    print(f"{'동':<12}{'시군구':<12}{'비율':>6}{'판정':>7}  근거")
    for r in rows[:20]:
        print(f"{(r['동'] or '')[:11]:<12}{(r['시군구'] or '')[:11]:<12}"
              f"{r['거주비율']:>6.2f}{r['판정']:>7}  {r['근거']}")

    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    jp = LOGS / f"apt_ratio_diagnosis_{stamp}.json"
    jp.write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"),
                              "tally": dict(tally), "rows": rows}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    cp = LOGS / f"apt_ratio_diagnosis_{stamp}.csv"
    # ⚠️ 엑셀은 BOM 이 없으면 UTF-8 한글을 깨서 연다 → utf-8-sig
    with cp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["adm_cd"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n[저장] {jp}\n[저장] {cp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
