# -*- coding: utf-8 -*-
"""
build_competitor.py — 경쟁사·자사 센터 지오코딩 + 집계 (항상 로더/크롤러 다음)

  입력  data/raw/competitor/manual_<ym>.json    load_competitor.py  (자사 = 정본)
        data/raw/competitor/crawl_<ym>.json     crawl_competitor.py (경쟁사 = 정본)
  출력  data/processed/competitor.json          지점 전건 (좌표·행정동 포함)
        data/processed/competitor_adm.json      행정동 집계 (metrics 조인용)
        data/processed/competitor_sgg.json      시군구 집계
        data/processed/competitor_history.json  지점별 first_seen/last_seen (신규·폐점 추적)

브랜드별 원천 선택 규칙
  같은 브랜드가 xlsx 와 크롤 양쪽에 있으면 **base_ym 이 최신인 쪽**을 쓴다.
  동률이면 크롤을 쓴다(사이트가 더 최신이다). 자사(와이키즈)는 크롤 대상이 아니라
  항상 xlsx 다.

사용법
  python scripts/build_competitor.py
  python scripts/build_competitor.py --no-guard     # 하드가드를 경고로만
  python scripts/build_competitor.py --no-geocode   # 캐시에 있는 것만 (오프라인)

⚠️ 하드가드 (B-11 과 같은 취지)
  경쟁사가 조용히 줄어드는 것은 **가장 위험한 방향의 오류**다.
  지도에서 "경쟁사 없는 좋은 상권"으로 보이고, 그 화면이 가맹상담에 쓰인다.
  그래서 ①브랜드별 min_expect 미달 ②지오코딩 성공률 95% 미만 ③행정동 미배정
  ④직전 회차 대비 20% 이상 급감 ⑤번호 유실 지오코딩(원본 건물번호·지번이 적중 후보에 없음 =
  다른 건물에 찍힘) 이면 **RuntimeError 로 중단**한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brands as B                                     # noqa: E402
from addr_norm import candidates, lost_number, missing_sgg   # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw" / "competitor"

OUT = PROC / "competitor.json"
OUT_ADM = PROC / "competitor_adm.json"
OUT_SGG = PROC / "competitor_sgg.json"
OUT_SIDO = PROC / "competitor_sido.json"
HIST = PROC / "competitor_history.json"

GEOCODE_MIN = 0.95          # 지오코딩 성공률 하한
DROP_MAX = 0.20             # 직전 회차 대비 허용 감소폭


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# --------------------------------------------------------------------------
# 원천 선택
# --------------------------------------------------------------------------

def pick_sources() -> tuple[dict[str, list[dict]], dict[str, str], str]:
    """브랜드별로 어느 파일을 쓸지 고른다 → ({brand_key: [rec]}, {brand_key: 설명}, base_ym)"""
    # ⚠️ `*.json` 으로 긁으면 안 된다 — 임시로 옮겨둔 파일(`_hold_...json`)까지
    #    원천으로 먹어서, 배제했다고 생각한 회차가 계속 채택된다(실제로 겪었다).
    files = sorted(list(RAW.glob("manual_*.json")) + list(RAW.glob("crawl_*.json")))
    if not files:
        raise SystemExit(
            "[FATAL] data/raw/competitor 가 비어 있다 — "
            "load_competitor.py 를 먼저 실행할 것")

    # (brand_key) → (rank, ym, source, recs)
    best: dict[str, tuple] = {}
    for f in files:
        j = read(f)
        if not j:
            continue
        ym = str((j.get("meta") or {}).get("base_ym") or "")
        kind = "crawl" if f.name.startswith("crawl_") else "xlsx"
        by_brand = defaultdict(list)
        for r in j.get("data") or []:
            by_brand[r["brand_key"]].append(r)
        for bk, recs in by_brand.items():
            # 동률이면 크롤 우선 (사이트가 더 최신)
            cand = (ym, 1 if kind == "crawl" else 0)
            if bk not in best or cand > best[bk][0]:
                best[bk] = (cand, f"{kind} {f.name} ({ym})", recs, ym)

    picked = {bk: v[2] for bk, v in best.items()}
    origin = {bk: v[1] for bk, v in best.items()}
    yms = sorted({v[3] for v in best.values() if v[3]})
    return picked, origin, (yms[-1] if yms else "")


# --------------------------------------------------------------------------

def geocode_all(recs: list[dict], enable: bool) -> dict:
    """후보 주소를 순서대로 시도한다. 하나라도 맞으면 통과."""
    stat = {"ok": 0, "fail": 0, "cache_only": 0, "by_cand": defaultdict(int)}

    if not enable:
        cache = (read(PROC / "geocode_cache.json") or {}).get("data") or {}
        for r in recs:
            used = next((c for c in r["addr_cands"] if cache.get(c)), None)
            if used:
                hit = cache[used]
                r.update(lat=hit["lat"], lng=hit["lng"], adm_cd=hit["adm_cd"], geo_used=used)
                stat["ok"] += 1
            else:
                stat["fail"] += 1
        stat["cache_only"] = 1
        return stat

    from geocode_addr import Geocoder                   # noqa: E402
    g = Geocoder(workers=8, verbose=True)

    # 1차: 모든 레코드의 첫 후보를 한 번에 (스레드 재사용으로 빠르다)
    g.batch([r["addr_cands"][0] for r in recs], progress_every=200)

    for r in recs:
        hit = None
        for i, c in enumerate(r["addr_cands"]):
            hit = g.one(c)
            if hit:
                stat["by_cand"][i] += 1
                break
        if hit:
            r.update(lat=hit["lat"], lng=hit["lng"], adm_cd=hit["adm_cd"], geo_used=c)
            stat["ok"] += 1
        else:
            stat["fail"] += 1
    g.save()
    return stat


def main(argv=None):
    ap = argparse.ArgumentParser(description="경쟁사 지오코딩·집계")
    ap.add_argument("--no-guard", action="store_true", help="하드가드를 경고로만")
    ap.add_argument("--no-geocode", action="store_true", help="캐시만 사용")
    a = ap.parse_args(argv)

    picked, origin, base_ym = pick_sources()
    recs: list[dict] = []
    for bk in [b["key"] for b in B.BRANDS]:
        if bk in picked:
            recs.extend(picked[bk])
            print(f"[build] {B.BY_KEY[bk]['name']:8s} {len(picked[bk]):4d}건  ← {origin[bk]}")

    missing = [b["name"] for b in B.BRANDS if b["key"] not in picked]
    if missing:
        print(f"[build] ⚠️ 원천이 없는 브랜드: {missing}")

    # 자료 대기 중인 축은 매 회차 눈에 띄게 남긴다 —
    # 지도의 "0개"가 실측 0인지 아직 안 넣은 것인지 구분이 안 되기 때문이다.
    for p in getattr(B, "PENDING", []):
        print(f"[build] ⬜ 대기 중 — {p['name']} "
              f"({B.CATEGORIES[p['category']]} · 대상 {'/'.join(p['brand_target'])}): {p['note']}")

    # 후보 주소는 **여기서 다시 만든다.**
    # raw json 에 저장된 addr_cands 를 그대로 쓰면 addr_norm 의 보정·정제 개선이
    # 옛 수집분에 반영되지 않는다(재크롤 없이는 영영 실패로 남는다).
    for r in recs:
        r["addr_cands"] = candidates(r["addr_raw"]) or r.get("addr_cands") or []

    # ---- 지오코딩 ----
    print(f"\n[build] 지오코딩 {len(recs):,}건"
          + ("  (캐시만)" if a.no_geocode else ""))
    st = geocode_all(recs, enable=not a.no_geocode)
    rate = st["ok"] / max(len(recs), 1)
    print(f"[build] 좌표 확보 {st['ok']:,}/{len(recs):,} ({rate*100:.1f}%) · 실패 {st['fail']:,}")
    if st["by_cand"]:
        print(f"[build] 후보 적중 분포 {dict(sorted(st['by_cand'].items()))}"
              "   (0=원본 · 뒤로 갈수록 많이 깎은 주소)")

    fails = [r for r in recs if not r.get("adm_cd")]
    for r in fails[:10]:
        # 시군구 누락은 실패 원인 1순위다 — 실패한 건에만 붙여야 노이즈가 안 된다
        why = "  ← 시군구 없음" if missing_sgg(r["addr_raw"]) else ""
        print(f"         ✗ {r['brand']}/{r['name']}  {r['addr_raw']}{why}")
    if len(fails) > 10:
        print(f"         ... 외 {len(fails)-10}건")

    # 번호를 잃은 후보로 찍힌 지점 — 좌표는 나와도 **다른 건물·동 대표점**이다 (addr_norm.lost_number)
    imprecise = [(r, lost_number(r["addr_raw"], r["geo_used"])) for r in recs if r.get("geo_used")]
    imprecise = [(r, n) for r, n in imprecise if n]
    for r, n in imprecise:
        print(f"         ⚠ 번호 유실 {r['brand']}/{r['name']}  {r['addr_raw']}"
              f"  → 적중 `{r['geo_used']}` (원본 번호 {n})")

    ok = [r for r in recs if r.get("adm_cd")]
    for r in ok:
        r.pop("geo_used", None)
        r["sgg_cd"] = r["adm_cd"][:5]
        r["sido_cd"] = r["adm_cd"][:2]
        r.pop("addr_cands", None)

    # ---- 이력 (신규·개명·이전·폐점 추적) ----
    #
    # ⚠️ 지점명만으로 추적하면 **개명이 폐점+신규로 둘 다 잡힌다.**
    #    2026-08 크롤에서 실제로 나왔다:
    #      소마 `광명소하소마` → `광명하안소마`   (주소 동일)
    #      소마 `소마성북학원`  → `성북소마`       (주소 동일)
    #    그래서 ①id(지점명) → ②dkey(브랜드+주소) 2단으로 맞춘다.
    #    반대로 이름은 같고 주소만 바뀌면 '이전'이다(필즈 인천/송도 165-14 → 164).
    hist = (read(HIST) or {}).get("data") or {}
    first_build = not hist
    ym_now = base_ym
    by_dkey = {h["dkey"]: hid for hid, h in hist.items() if h.get("dkey")}

    now_ids = set()
    new_ids, renamed, moved = [], [], []
    for r in ok:
        ym = r.get("base_ym") or ym_now
        h = hist.get(r["id"])
        if h is None:
            hid = by_dkey.get(r["dkey"])              # 주소가 같은 옛 지점 = 개명
            if hid and hid not in now_ids:
                h = hist.pop(hid)
                renamed.append((h["name"], r["name"], r["brand"]))
                hist[r["id"]] = h
        if h is None:
            hist[r["id"]] = h = {"first_seen": ym, "brand": r["brand"]}
            if not first_build:
                new_ids.append(f"{r['brand']}/{r['name']}")
        elif h.get("dkey") and h["dkey"] != r["dkey"]:
            moved.append((r["brand"], r["name"], h.get("addr", ""), r["addr_raw"]))

        h.update(last_seen=ym, name=r["name"], addr=r["addr_raw"],
                 dkey=r["dkey"], brand=r["brand"], status="active")
        # 지오코딩 실패로 한 회차 빠졌다가 돌아온 지점 — 폐점 표시를 지워야
        # 이력이 'closed 인데 active' 인 모순 상태로 남지 않는다.
        h.pop("closed_at", None)
        r["first_seen"] = h["first_seen"]
        now_ids.add(r["id"])

    # 이번 회차에 안 보인 지점 = 폐점 후보.
    # ⚠️ 크롤이 통째로 실패한 브랜드는 제외해야 한다 — 안 그러면 사이트 장애가
    #    "그 브랜드 전 지점 폐점"으로 기록된다.
    live_brands = {r["brand"] for r in ok}
    closed = []
    for hid, h in hist.items():
        if hid in now_ids or h.get("brand") not in live_brands:
            continue
        if h.get("status") != "closed":
            h["status"] = "closed"
            h["closed_at"] = ym_now
            closed.append(f"{h.get('brand')}/{h.get('name')}")

    for r in ok:
        r.pop("dkey", None)

    # ---- 집계 ----
    def agg(level_key: str):
        out: dict[str, dict] = {}
        for r in ok:
            k = r[level_key]
            d = out.setdefault(k, {"own": 0, "competitor": 0, "english_kinder": 0,
                                   "total": 0, "by_brand": {}})
            d[r["category"]] += 1
            d["total"] += 1
            d["by_brand"][r["brand"]] = d["by_brand"].get(r["brand"], 0) + 1
        return out

    adm, sgg, sido = agg("adm_cd"), agg("sgg_cd"), agg("sido_cd")

    # 행정동 코드가 metrics 에 실재하는지 (대시보드 조인 실패 = 지도에서 증발)
    met = read(PROC / "metrics.json")
    known = set((met or {}).get("data") or {})
    orphan = [k for k in adm if known and k not in known]

    cnt_brand = {b["name"]: sum(1 for r in ok if r["brand"] == b["name"]) for b in B.BRANDS}
    cnt_cat = {c: sum(1 for r in ok if r["category"] == c) for c in B.CATEGORIES}

    print(f"\n[build] 행정동 {len(adm):,}개 · 시군구 {len(sgg):,}개")
    print(f"[build] 카테고리 {cnt_cat}")
    print(f"[build] 변동 — 신규 {len(new_ids)} · 개명 {len(renamed)} · "
          f"이전 {len(moved)} · 폐점 {len(closed)}")
    for a_, b_, br in renamed:
        print(f"         ↻ {br} {a_} → {b_}")
    for br, nm, o, n in moved:
        print(f"         ⇢ {br} {nm}: {o[:34]} → {n[:34]}")
    # 브랜드를 새로 등재한 회차는 그 브랜드 전 지점이 '신규'다(와이즈만 95 + 황소 83).
    # 전부 찍으면 진짜 변동이 스크롤 밖으로 밀려 안 보인다 → 앞부분만 보여준다.
    for x in new_ids[:20]:
        print(f"         + {x}")
    if len(new_ids) > 20:
        print(f"         ... 신규 외 {len(new_ids)-20}건")
    for x in closed:
        print(f"         − {x}")

    # ---- 하드가드 ----
    prev = read(OUT)
    viol = []
    for b in B.BRANDS:
        n = cnt_brand.get(b["name"], 0)
        if b["key"] in picked and n < b["min_expect"]:
            viol.append(f"{b['name']} {n}건 < min_expect {b['min_expect']}")
        if prev:
            pn = (prev.get("meta") or {}).get("counts", {}).get(b["name"])
            if pn and n < pn * (1 - DROP_MAX):
                viol.append(f"{b['name']} {pn} → {n} ({(n/pn-1)*100:.0f}%) 급감")
    if rate < GEOCODE_MIN and not a.no_geocode:
        viol.append(f"지오코딩 성공률 {rate*100:.1f}% < {GEOCODE_MIN*100:.0f}%")
    if orphan:
        viol.append(f"경계에 없는 행정동 {len(orphan)}건: {orphan[:5]}")
    if imprecise:
        viol.append(f"번호 유실 지오코딩 {len(imprecise)}건 (다른 건물에 찍힘): "
                    f"{[r['brand'] + '/' + r['name'] for r, _ in imprecise[:5]]}"
                    " → JUSO 로 도로명 확인 후 addr_norm.MANUAL_FIX(_PREFIX) 등재")

    if viol:
        msg = "[build] 하드가드 위반:\n  - " + "\n  - ".join(viol)
        if a.no_guard:
            print("⚠️ " + msg + "\n   (--no-guard 라 계속한다)")
        else:
            raise RuntimeError(
                msg + "\n\n경쟁사가 조용히 줄면 지도가 '경쟁 없는 좋은 상권'을 만든다."
                      "\n원인을 확인하고, 의도한 감소라면 --no-guard 로 다시 실행할 것.")

    # ---- 출력 ----
    meta = {
        "sample": False,
        "updated": date.today().isoformat(),
        "base_ym": base_ym,
        "source": origin,
        "counts": cnt_brand,
        "by_category": cnt_cat,
        "geocode": {"ok": st["ok"], "fail": st["fail"], "rate": round(rate, 4),
                    "imprecise": [f"{r['brand']}/{r['name']}" for r, _ in imprecise]},
        "brand_target": {b["name"]: b["brand_target"] for b in B.BRANDS},
        # 자사가 두 브랜드(와이키즈·와이즈만)라 화면이 "자사 = 와이키즈"로
        # 하드코딩돼 있으면 와이즈만이 경쟁사처럼 보인다 → 목록으로 넘긴다.
        "own_brands": [b["name"] for b in B.of_category("own")],
        # 시너지세권(영어유치원)은 경쟁사가 아니다 — 표시명·합산 금지 근거를 데이터에도 남긴다
        "category_label": B.CATEGORIES,
        "category_role": B.CATEGORY_ROLE,
        "category_source": B.CATEGORY_SOURCE,
        # 아직 자료가 없는 축. "0개"가 실측인지 미수령인지 구분하는 유일한 근거다
        "pending": getattr(B, "PENDING", []),
        "changes": {"new": new_ids, "renamed": [list(x) for x in renamed],
                    "moved": [list(x) for x in moved], "closed": closed},
    }
    PROC.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"meta": meta, "data": ok}, ensure_ascii=False), encoding="utf-8")
    OUT_ADM.write_text(json.dumps({"meta": {**meta, "level": "adm"}, "data": adm},
                                  ensure_ascii=False), encoding="utf-8")
    OUT_SGG.write_text(json.dumps({"meta": {**meta, "level": "sgg"}, "data": sgg},
                                  ensure_ascii=False), encoding="utf-8")
    OUT_SIDO.write_text(json.dumps({"meta": {**meta, "level": "sido"}, "data": sido},
                                   ensure_ascii=False), encoding="utf-8")
    HIST.write_text(json.dumps({"meta": {"updated": date.today().isoformat()},
                                "data": hist}, ensure_ascii=False), encoding="utf-8")
    print(f"\n[build] 출력 {OUT.name} / {OUT_ADM.name} / {OUT_SGG.name} / "
          f"{OUT_SIDO.name} / {HIST.name}")
    if fails:
        print(f"⚠️ 좌표 실패 {len(fails)}건은 지도·집계에서 빠진다 — 주소를 고쳐 재실행할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
