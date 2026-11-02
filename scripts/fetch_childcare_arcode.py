# -*- coding: utf-8 -*-
"""fetch_childcare_arcode.py — 어린이집 포털의 **시군구 코드표**를 받아 오버라이드와 대조 (읽기 전용)

    python scripts/fetch_childcare_arcode.py            대조 결과만 출력
    python scripts/fetch_childcare_arcode.py --json      원본 표를 JSON 으로

왜 있나
    포털(cpmsapi030)의 `arcode` 는 **행안부 표준 시군구코드가 아니다.** 시 승격·구 신설
    이전 코드나 자체 연번을 그대로 쓴다. 표준코드로 물으면 HTTP 200 + INFO-200 이 와서
    **"어린이집 0개"와 구분이 안 된다**(조용한 실패).
    2026-08-12 에는 이걸 코드 스캔으로 찾다가 **호출 한도**에 걸려 되던 코드까지 0건이 됐다.

    포털 화면이 쓰는 코드 목록 페이지는 **API 한도를 안 쓴다.** 그래서 여기서 받아
    `collect_childcare.ARCODE_OVERRIDE` 와 대조한다 — 추측·스캔이 필요 없다.

    ⚠️ 이 표는 **시 단위까지**다(용인시·천안시·포항시). 구 단위 코드는 포털 화면에서만
       내려오므로 구 오버라이드(41462 등)는 여기서 확인되지 않는다 — 그건 '참고'로만 찍는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SIDO_URL = "https://info.childcare.go.kr/info_html5/pnis/search/areaSearchList.jsp"
SGG_URL = "https://info.childcare.go.kr/info_html5/cera/common/areaCodeList.jsp?flag=sigungu&arCode={}"
UA = {"User-Agent": "Mozilla/5.0", "Referer": SIDO_URL}


def get(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
        return r.read().decode("utf-8", "replace")


def sido_codes() -> list[tuple[str, str]]:
    h = get(SIDO_URL)
    m = re.search(r'<select[^>]*id="ctprvn".*?</select>', h, re.S)
    if not m:
        raise SystemExit("[!!] 시도 select 를 못 찾았다 — 포털 화면이 바뀐 것이다.")
    return [(c, n.strip()) for c, n in re.findall(r'value="([0-9]{5})"[^>]*>\s*([^<]+)', m.group(0))]


def sgg_codes(sido: str) -> list[tuple[str, str]]:
    h = get(SGG_URL.format(sido))
    return [(c, n.strip()) for c, n in re.findall(r'value="?([0-9]{5})"?[^>]*>\s*([^<]{1,20}?)\s*<', h)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="어린이집 포털 시군구 코드표 조회·대조")
    ap.add_argument("--json", action="store_true", help="원본 표를 JSON 으로 출력")
    a = ap.parse_args(argv)

    table: dict[str, list[tuple[str, str]]] = {}
    for code, name in sido_codes():
        try:
            rows = sgg_codes(code)
        except Exception as e:  # noqa: BLE001
            print(f"  [건너뜀] {code} {name}: {type(e).__name__}")
            continue
        table[code] = rows
        print(f"  {code} {name:<14} 시군구 {len(rows):>2}개")

    if a.json:
        print(json.dumps({k: dict(v) for k, v in table.items()}, ensure_ascii=False, indent=1))
        return 0

    portal = {c: n for rows in table.values() for c, n in rows}
    from collect_childcare import ARCODE_OVERRIDE, ARCODE_UNRESOLVED  # noqa: PLC0415

    print()
    print(f"[대조] 포털 시군구 {len(portal)}개 ↔ ARCODE_OVERRIDE {len(ARCODE_OVERRIDE)}건")
    for std, ask in sorted(ARCODE_OVERRIDE.items()):
        if ask in portal:
            print(f"   ✔ {std} → {ask} {portal[ask]}")
        else:
            print(f"   · {std} → {ask} (구 단위 — 이 표에는 없다. 확인 대상 아님)")
    unknown = [c for c in ARCODE_UNRESOLVED]
    if unknown:
        print()
        print("[미해소] 아래는 아직 코드를 모른다 — 위 표에서 같은 이름을 찾아 오버라이드에 넣을 것:")
        for c in unknown:
            print(f"   {c} {ARCODE_UNRESOLVED[c]}")
    else:
        print("[미해소] 없음 — 전 시군구 코드가 확정돼 있다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
