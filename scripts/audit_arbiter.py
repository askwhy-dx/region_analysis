# -*- coding: utf-8 -*-
"""audit_arbiter.py — E-4 JUSO 심판 판정 전수 점검 — **읽기 전용**

    python scripts/audit_arbiter.py                 캐시만으로 (호출 0회)
    python scripts/audit_arbiter.py --fill          판단불가 건만 JUSO 추가 조회
    python scripts/audit_arbiter.py --fill --limit 200

무엇을 잡나
    E-4 규칙은 "폴리곤과 통계청이 갈리면 **행안부(JUSO)가 정본**"이다. 923건은 이 규칙
    덕분에 맞게 옮겨졌다(여의동→혁신동 81건 = 분동 반영 지연). 그런데 **행안부 원본에도
    한 번지만 틀린 레코드가 있다.**

    실측 2026-08-12 · 광주 광산구 장신로147번길
        5-12  → JUSO 행정동 '하남동'   ← 이 한 건만
        5-18  → '수완동'      10 → '수완동'      139 → '수완동'
      좌표 PIP·법정동·현장 확인 전부 수완동. 그런데 심판을 무조건 채택해서
      '생각하는황소 광주수완'이 하남동에 1개로 계상됐다. 지도 마커는 좌표(수완동)에
      찍히므로 **선택한 동에는 없는 경쟁사가 카운트만 잡히는** 모양이 된다.

어떻게 판정하나 — **JUSO 자신에게 이웃을 물어본다**
    같은 도로명·가까운 본번의 다른 주소들에 대해 JUSO 가 뭐라 답했는지 본다.
      · 이웃 다수도 같이 옮겼다      → CONFIRMED (진짜 경계 지연 · 채택이 맞다)
      · 이웃 다수는 PIP 편인데 혼자  → **OUTLIER** (원본 오류 의심 · PIP 유지가 맞다)
      · 이웃 표본이 없다             → UNKNOWN (--fill 로 JUSO 에 직접 물어 채운다)

    ⚠️ 이웃은 **번호가 가까운 것만** 쓴다. 긴 도로(예: 인계로)는 실제로 여러 동을 지나서,
       도로명만 같다고 이웃 취급하면 멀쩡한 판정을 외톨이로 몰 수 있다.

산출: data/logs/audit_arbiter_YYYYMMDD.json  (요약은 화면)
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
LOGS = ROOT / "data" / "logs"
API = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
NEAR = 5          # 본번 차이가 이 안쪽이면 '이웃'
NEAR_WIDE = 20    # 가까운 이웃이 없을 때만 넓힌다


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def norm(s: str) -> str:
    """'용호제2동'→'용호2동', 공백 제거. adm_arbiter.py 와 같은 규칙."""
    s = (s or "").strip()
    s = re.sub(r"제(\d)", r"\1", s)
    return re.sub(r"\s+", "", s)


def dongs(hemd: str) -> set[str]:
    """행정동 이름 집합. **`hemdNm` 은 '전농2동,휘경2동' 처럼 여러 동을 줄 수 있다.**
    한 덩어리 문자열로 비교하면 그런 건물이 전부 '불일치'로 잘못 잡힌다(실측).
    앞에 붙는 시도·시군구는 떼고 **마지막 토큰(동 이름)** 만 남긴다.

    ⚠️ **공백을 먼저 지우면 안 된다.** '강원특별자치도 동해시 동호동' 을 붙여 버리면
       마지막 토큰 추출이 통째로 먹혀('강원특별자치도동해시동호동') 전건이 불일치가 된다.
       토큰을 자른 **뒤에** 정규화한다."""
    out = set()
    for part in (hemd or "").split(","):
        toks = part.strip().split()
        if not toks:
            continue
        out.add(norm(toks[-1]))
    return out


def split_addr(a: str) -> tuple[str | None, int | None]:
    """'… 장신로147번길 5-12' → ('… 장신로147번길', 5)"""
    m = re.match(r"^(.*?(?:\d*번길|로|길))\s+(\d+)", a)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def juso_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("JUSO_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("[!!] .env 에 JUSO_KEY 가 없다")


def juso_road(key: str, road: str, cache: dict) -> list[dict]:
    """도로명 하나로 최대 100건을 한 번에 받는다 — 주소마다 부르면 호출이 100배가 된다."""
    if road in cache:
        return cache[road]
    q = urllib.parse.urlencode({"confmKey": key, "currentPage": 1, "countPerPage": 100,
                                "keyword": road, "resultType": "json", "hstryYn": "N",
                                "addInfoYn": "Y"})
    out = []
    try:
        with urllib.request.urlopen(f"{API}?{q}", timeout=25) as r:
            d = json.loads(r.read().decode("utf-8"))
        for j in (d.get("results") or {}).get("juso") or []:
            try:
                out.append({"mnnm": int(j.get("buldMnnm") or -1), "hemd": j.get("hemdNm") or ""})
            except (TypeError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        out = []
    cache[road] = out
    time.sleep(0.15)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E-4 심판 판정 전수 점검 (읽기 전용)")
    ap.add_argument("--fill", action="store_true", help="판단불가 건을 JUSO 에 직접 물어 채운다")
    ap.add_argument("--limit", type=int, default=0, help="--fill 시 최대 조회 도로 수")
    a = ap.parse_args(argv)

    verdict = read(PROC / "juso_arbiter.json").get("data") or {}
    names = {c: v.get("name") for c, v in read(PROC / "metrics.json")["data"].items()}
    print(f"[대상] 심판 판정 캐시 {len(verdict):,}건")

    # ---- 심판이 실제로 '옮긴' 건만 고른다 ------------------------------------
    # hemd(이름) 와 pip(코드) 를 이름으로 맞춰 본다. 이름이 같으면 안 옮긴 것이다.
    moved = []
    for addr, v in verdict.items():
        hemd, pip = v.get("hemd"), v.get("pip")
        if not hemd or not pip:
            continue
        pip_nm = names.get(pip)
        if not pip_nm:
            continue
        if norm(pip_nm) in dongs(hemd):
            continue                      # JUSO 도 PIP 와 같은 동 → 분쟁 없음
        moved.append((addr, hemd, pip, pip_nm))
    print(f"[분쟁] JUSO 가 PIP 와 다른 결론을 낸 건 {len(moved):,}건")

    # ---- 캐시 안에서 이웃 찾기 (호출 0회) ------------------------------------
    by_road: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    for addr, v in verdict.items():
        road, mnnm = split_addr(addr)
        if road and mnnm is not None and v.get("hemd"):
            by_road[road].append((mnnm, v["hemd"]))

    key = juso_key() if a.fill else ""
    road_cache: dict[str, list[dict]] = {}
    filled = 0
    rows = []
    for addr, hemd, pip, pip_nm in moved:
        road, mnnm = split_addr(addr)
        peers: list[tuple[int, str]] = []
        if road:
            peers = [(m, h) for m, h in by_road.get(road, [])
                     if h and not (m == mnnm and norm(h) == norm(hemd))]
        near = [h for m, h in peers if abs(m - (mnnm or 0)) <= NEAR]
        if not near:
            near = [h for m, h in peers if abs(m - (mnnm or 0)) <= NEAR_WIDE]
        src = "cache"

        if not near and a.fill and road and (not a.limit or filled < a.limit):
            got = juso_road(key, road, road_cache)
            if got:
                filled += 1
                src = "juso"
                near = [g["hemd"] for g in got
                        if g["hemd"] and abs(g["mnnm"] - (mnnm or 0)) <= NEAR
                        and g["mnnm"] != mnnm]
                if not near:
                    near = [g["hemd"] for g in got
                            if g["hemd"] and abs(g["mnnm"] - (mnnm or 0)) <= NEAR_WIDE
                            and g["mnnm"] != mnnm]

        if not near:
            verd = "UNKNOWN"
            top = None
        else:
            cnt = collections.Counter()
            for h in near:
                cnt.update(dongs(h))
            top, _ = cnt.most_common(1)[0]
            if top in dongs(hemd):
                verd = "CONFIRMED"
            elif top == norm(pip_nm):
                verd = "OUTLIER"
            else:
                verd = "UNKNOWN"
        rows.append({"addr": addr, "juso_hemd": hemd, "pip_cd": pip, "pip_name": pip_nm,
                     "verdict": verd, "peers": len(near), "peer_top": top, "src": src})

    tally = collections.Counter(r["verdict"] for r in rows)
    print()
    print(f"  CONFIRMED (채택이 맞다) {tally['CONFIRMED']:>5}건")
    print(f"  OUTLIER   (원본 오류 의심) {tally['OUTLIER']:>5}건   ← 되돌릴 대상")
    print(f"  UNKNOWN   (표본 부족)      {tally['UNKNOWN']:>5}건")
    if a.fill:
        print(f"  (JUSO 추가 조회한 도로 {filled}개)")

    out = [r for r in rows if r["verdict"] == "OUTLIER"]
    out.sort(key=lambda r: -r["peers"])
    print("\n[OUTLIER] 이웃 근거가 많은 순 20건")
    for r in out[:20]:
        print(f"  {r['addr']}")
        print(f"     JUSO '{r['juso_hemd'].split()[-1]}' vs PIP '{r['pip_name']}'"
              f" · 이웃 {r['peers']}건 다수 '{r['peer_top']}' ({r['src']})")

    LOGS.mkdir(parents=True, exist_ok=True)
    p = LOGS / f"audit_arbiter_{datetime.now():%Y%m%d}.json"
    p.write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"),
                             "tally": dict(tally), "rows": rows}, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    print(f"\n[저장] {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
