# -*- coding: utf-8 -*-
"""check_vworld_key.py — VWorld 위성 타일 진단 — **읽기 전용**

    python scripts/check_vworld_key.py            기본 진단
    python scripts/check_vworld_key.py --tiles 30 부하를 조금 더 줘서 본다

무엇을 가리나 — "위성 지도가 안 나온다"의 원인은 셋인데 증상이 똑같다
  ① VWorld 서버 장애    → **키 없이 호출해도** 안 된다 (2026-08-11 실제로 503)
  ② 우리 키 문제·한도   → 키 없이는 되는데 우리 키로만 안 된다
  ③ 우리 쪽 네트워크    → 둘 다 안 되고 다른 사이트도 느리다

그래서 **같은 타일을 키 있이/없이 둘 다** 불러 보고 비교한다. 이 비교가 없으면
"우리 키가 죽었나" 하고 재발급부터 하게 되는데, 대개는 그 사이 서버가 복구된다.

⚠️ 대시보드가 **공개**라 `web/config.json` 의 키가 그대로 노출된다. WMTS 는 Referer 를
   강제하지 않아서(실측) **남이 우리 한도를 쓸 수 있다.** 한도 소진이 의심되면 재발급이
   유일한 해법이다 — 이 스크립트는 그 판단 근거를 만든다.

⚠️ 타일은 「지역 × 줌」마다 다른 파일이다. 한 곳이 된다고 다 되는 게 아니라서
   서울·부산·광주·제주를 줌 12~17 로 흩어 뽑는다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# 흩어 뽑을 지점 (서울 강남 · 부산 해운대 · 광주 수완 · 제주시)
SPOTS = [("서울 강남", 127.0473, 37.5172), ("부산 해운대", 129.1600, 35.1631),
         ("광주 수완", 126.8249, 35.1914), ("제주시", 126.5312, 33.4996)]
ZOOMS = [12, 14, 16, 17]


def deg2tile(lng: float, lat: float, z: int) -> tuple[int, int]:
    n = 2.0 ** z
    x = int((lng + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return x, y


def key_from_config() -> str | None:
    p = WEB / "config.json"
    if p.exists():
        try:
            return (json.loads(p.read_text(encoding="utf-8")) or {}).get("vworldKey")
        except Exception:  # noqa: BLE001
            pass
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("VWORLD_KEY="):
            return line.split("=", 1)[1].strip()
    return None


def fetch(url: str, timeout: int = 15):
    """(상태, 바이트수, 메모). 타일은 이미지라 **본문 앞 몇 바이트로 진짜 이미지인지** 본다 —
    한도 초과·키 오류는 200 에 에러 텍스트를 실어 보내는 경우가 있다."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            ms = int((time.time() - t0) * 1000)
            head = body[:16]
            if head.startswith(b"\xff\xd8") or head.startswith(b"\x89PNG"):
                return r.status, len(body), f"이미지 {ms}ms"
            txt = " ".join(body[:200].decode("utf-8", "replace").split())
            return r.status, len(body), f"⚠️ 이미지가 아님: {txt[:120]}"
    except urllib.error.HTTPError as e:
        return e.code, 0, " ".join(e.read(200).decode("utf-8", "replace").split())[:120]
    except Exception as e:  # noqa: BLE001
        return 0, 0, f"{type(e).__name__}: {e}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VWorld 위성 타일 진단 (읽기 전용)")
    ap.add_argument("--tiles", type=int, default=0, help="추가로 더 뽑을 타일 수 (부하 확인용)")
    a = ap.parse_args(argv)

    key = key_from_config()
    if not key:
        print("[!!] VWorld 키를 찾지 못했다 (.env 의 VWORLD_KEY / web/config.json)")
        return 1
    print(f"[키] …{key[-6:]} (web/config.json 기준 = 공개 페이지가 쓰는 값)")

    ok = fail = 0
    slow = []
    print("\n[1] 우리 키로 타일 받기")
    for name, lng, lat in SPOTS:
        for z in ZOOMS:
            x, y = deg2tile(lng, lat, z)
            url = f"https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg"
            st, n, memo = fetch(url)
            good = st == 200 and n > 500 and memo.startswith("이미지")
            ok, fail = ok + good, fail + (not good)
            ms = int(memo.split()[1].rstrip("ms")) if good else None
            if ms and ms > 3000:
                slow.append((name, z, ms))
            print(f"  {name:<8} z{z:<3} {'OK ' if good else '실패'} {st} {n:>7,}B  {memo}")
            time.sleep(0.1)

    print(f"\n  성공 {ok} · 실패 {fail}")
    if slow:
        print(f"  느린 타일 {len(slow)}건 (3초 초과) — 네트워크가 좁을 때 상담 중 끊겨 보인다")

    # ---- 키 없이도 안 되는가 = 서버 장애 판별 --------------------------------
    print("\n[2] 키 없이 같은 타일 (서버가 살아 있는지)")
    x, y = deg2tile(*SPOTS[0][1:], 14)
    st, n, memo = fetch(f"https://api.vworld.kr/req/wmts/1.0.0/NOKEY/Satellite/14/{y}/{x}.jpeg")
    print(f"  키 없이 호출 → {st} {n:,}B  {memo}")

    print("\n[판정]")
    if fail == 0:
        print("  ✅ 우리 키로 전 지점·전 줌이 정상이다. 위성이 안 보였다면")
        print("     ① 그 시점에만 장애였거나 ② 사무실 네트워크 쪽이다.")
        print("     (같은 시각에 일반 지도는 잘 나왔는지 확인하면 갈린다)")
    elif fail and st in (0, 500, 502, 503):
        print("  ⚠️ 키 없이도 서버가 응답하지 않는다 → **VWorld 장애**로 보인다.")
        print("     우리가 할 수 있는 건 없다. 몇 시간이면 복구된다(2026-08-11 전례).")
    else:
        print("  ⚠️ 키 없이는 응답이 오는데 우리 키로 실패했다 → **키 문제(한도·정지·도메인)**")
        print("     대시보드가 공개라 키가 노출돼 있어 남이 한도를 쓸 수 있다.")
        print("     vworld.kr > 마이페이지 > 인증키 관리 에서 사용량을 확인하고, 소진이면 재발급할 것.")
        print("     재발급 후:  python scripts\\make_web_config.py  →  publish.bat")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
