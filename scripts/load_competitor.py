# -*- coding: utf-8 -*-
"""
load_competitor.py — 자사·경쟁사·시너지세권 위치 xlsx 로더

관리자가 2026-07 까지 **수기로 조사한 xlsx** 를 파이프라인 스키마로 변환한다.
2026-08 부터는 `crawl_competitor.py` 가 같은 스키마를 사이트에서 직접 만들지만,
**자사(와이키즈)는 크롤 대상이 아니라 계속 이 로더가 정본**이다.

  입력   와이키즈경쟁사_위치.xlsx   (시트 = 브랜드, 컬럼 = 브랜드명·센터명·도로명주소·데이터기준)
         와이즈만 위치.xlsx        (시트 `와이즈만`, 컬럼 = 번호·지역·센터명·센터장명·주소)
  출력   data/raw/competitor/manual_<ym>.json          (--preset waikids)
         data/raw/competitor/manual_wiseman_<ym>.json  (--preset wiseman)
  보존   data/manual/competitor/<원본파일명>  + sha256 (DATA_CONTRACT §0-8)

사용법
  python scripts/load_competitor.py                          # = --preset waikids
  python scripts/load_competitor.py --preset wiseman
  python scripts/load_competitor.py --src "C:\\...\\와이즈만 위치.xlsx" --tag wiseman
  python scripts/load_competitor.py --dry-run                # 파일 안 쓰고 진단만

⚠️ 함정
  * 시트명이 브랜드 정의(`brands.py`)와 안 맞으면 **통째로 조용히 빠진다.**
    그래서 미등록 시트를 만나면 경고가 아니라 **에러로 중단**한다.
    (내용이 아예 없는 빈 시트만 예외 — 엑셀이 남긴 `시트1` 류가 실제로 있다)
  * **출력 파일명이 겹치면 앞 자료가 지워진다.** 자사 브랜드가 둘(와이키즈·와이즈만)이고
    원본 xlsx 도 둘이라, 둘 다 `manual_<ym>.json` 으로 쓰면 나중 것이 앞 것을 덮는다
    → 한 브랜드가 통째로 지도에서 사라진다. `--tag` 로 파일명을 갈라 둔다.
  * `데이터기준`(base_ym) 이 시트마다 다를 수 있다 — 섞이면 "몇 월 기준 지도인지"를
    말할 수 없게 되므로 혼재 시 경고하고 최빈값을 meta 에 남긴다.
    **컬럼 자체가 없는 원본**(와이즈만)은 `--base-ym`, 없으면 **원본 파일의 수정월**을 쓴다.
    "관리자가 준 파일이 언제 것인가"가 곧 기준월이라 이게 가장 정직하다.
  * 좌표·행정동은 여기서 안 붙인다. `build_competitor.py` 가 한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

try:
    import openpyxl
except ImportError:
    print("[FATAL] openpyxl 필요:  pip install openpyxl", file=sys.stderr)
    raise

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brands as B                                    # noqa: E402
from addr_norm import candidates, dedupe_key, sido_of  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "competitor"
MANUAL = ROOT / "data" / "manual" / "competitor"

# 관리자 조사본 (없으면 --src 로 지정)
#
#   tag      출력 파일명 접미 — 빈 문자열이면 기존 `manual_<ym>.json`
#   sheet    시트가 브랜드명이 아닐 때 강제로 붙일 브랜드 (None = 시트명으로 판정)
#
# ⚠️ 원본이 둘이므로 tag 를 반드시 갈라 둘 것 (같은 파일명이면 덮어쓴다).
DESKTOP = Path(r"C:\Users\User\Desktop")

PRESETS = {
    "waikids": {
        "tag": "",
        "src": [DESKTOP / "와이키즈경쟁사_위치.xlsx",
                MANUAL / "와이키즈경쟁사_위치.xlsx"],
        "glob": "와이키즈경쟁사_위치*.xlsx",
    },
    "wiseman": {
        "tag": "wiseman",
        # ⚠️ `와이즈만 주소.xlsx`(73건)가 아니라 **`와이즈만 위치.xlsx`(95건)** 다.
        #    앞 파일은 부분 명부라 자사 센터가 있는 지역이 빈칸으로 나온다.
        #    glob 패턴도 '위치' 로 못박아 '주소' 파일이 절대 안 걸리게 한다.
        "src": [DESKTOP / "와이즈만 위치.xlsx",
                MANUAL / "와이즈만 위치.xlsx"],
        "glob": "와이즈만 위치*.xlsx",
    },
}
DEFAULT_PRESET = "waikids"

# 원본마다 헤더 표기가 다르다 — 같은 뜻이면 같은 필드로 받는다.
COLS = {
    "브랜드명": "brand",
    "센터명": "name", "지점명": "name", "캠퍼스명": "name",
    "도로명주소": "addr", "주소": "addr",
    "데이터기준": "base_ym",
}


def norm_name(s: str) -> str:
    """지점 식별 키 — 월 갱신 때 같은 지점을 이어 붙이는 용도.
    공백·'센터/캠퍼스/지점' 접미를 떼어 표기 흔들림을 흡수한다.

    ⚠️ **괄호 안 내용을 지우지 말 것.** 소마에 `강서마곡소마` 와 `(신)강서마곡소마`
       두 지점이 실재하는데, 괄호를 통째로 지우면 둘이 같은 id 가 되어
       매달 서로의 주소를 뺏는 '이전' 이벤트가 무한히 생긴다.
       괄호 기호만 없애고 글자는 남긴다.
    """
    s = re.sub(r"[()\[\]{}]", "", str(s or ""))
    s = re.sub(r"(센터|캠퍼스|지점|점)\s*$", "", s.strip())
    return re.sub(r"\s+", "", s).lower()


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_src(explicit: str | None, preset: str) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"[FATAL] 원본을 못 찾겠다: {p}")
        return p
    for p in PRESETS[preset]["src"]:
        if p.exists():
            return p

    # 관리자가 파일명에 날짜를 붙여 주는 일이 있다("와이즈만 위치_2609.xlsx").
    # 정확한 이름이 없으면 **가장 최근 수정본**을 고르고, 무엇을 골랐는지 반드시 찍는다.
    pat = PRESETS[preset].get("glob")
    if pat:
        hits = sorted(list(DESKTOP.glob(pat)) + list(MANUAL.glob(pat)),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            print(f"[load] ⚠️ 기본 파일명이 없어 패턴 '{pat}' 로 찾았다 → {hits[0].name}"
                  + (f"  (후보 {len(hits)}개 중 최신)" if len(hits) > 1 else ""))
            return hits[0]

    raise SystemExit(
        f"[FATAL] --preset {preset} 의 원본 xlsx 를 못 찾겠다 — --src 로 경로를 지정할 것\n"
        + "".join(f"        찾아본 곳: {p}\n" for p in PRESETS[preset]["src"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="경쟁사 위치 xlsx 로더")
    ap.add_argument("--preset", choices=sorted(PRESETS), default=DEFAULT_PRESET,
                    help="원본 프리셋 (기본 waikids)")
    ap.add_argument("--src", help="xlsx 경로 (프리셋 기본 경로 대신)")
    ap.add_argument("--tag", help="출력 파일명 접미 (기본 = 프리셋 값)")
    ap.add_argument("--base-ym", help="기준월 YYYYMM — 원본에 '데이터기준' 컬럼이 없을 때")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    src = find_src(a.src, a.preset)
    tag = a.tag if a.tag is not None else PRESETS[a.preset]["tag"]
    print(f"[load] 원본  {src}")
    wb = openpyxl.load_workbook(src, data_only=True)

    recs: list[dict] = []
    per_brand: dict[str, int] = {}
    ym_seen = Counter()
    problems = defaultdict(list)
    unknown_sheets = []

    for ws in wb.worksheets:
        bd = B.resolve(ws.title)
        if not bd:
            # 내용이 아예 없는 시트는 통째 누락이 아니라 엑셀이 남긴 껍데기다
            # (`와이즈만 위치.xlsx` 에 빈 `시트1` 이 실제로 있다).
            if not any(any(c is not None and str(c).strip() for c in row)
                       for row in ws.iter_rows(values_only=True)):
                print(f"[load] 빈 시트 '{ws.title}' 건너뜀")
                continue
            unknown_sheets.append(ws.title)
            continue

        # 헤더 매핑 — 컬럼 순서가 바뀌어도 이름으로 찾는다
        head = [str(c.value or "").strip() for c in ws[1]]
        idx = {COLS[h]: i for i, h in enumerate(head) if h in COLS}
        missing = {"name", "addr"} - set(idx)
        if missing:
            raise SystemExit(
                f"[FATAL] 시트 '{ws.title}' 에 필수 컬럼이 없다: {missing} (헤더={head[:6]})")

        n = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            def cell(k):
                i = idx.get(k)
                return str(row[i]).strip() if i is not None and row[i] is not None else ""

            name, addr = cell("name"), cell("addr")
            if not name and not addr:
                continue                                  # 빈 행
            if not addr:
                problems["주소없음"].append(f"{bd['name']}/{name}")
                continue

            ym = cell("base_ym") or ""
            ym_seen[ym] += 1
            cands = candidates(addr)
            if not cands:
                problems["정제실패"].append(f"{bd['name']}/{name}: {addr}")
                continue

            recs.append({
                "id": f"{bd['key']}:{norm_name(name)}",
                "dkey": f"{bd['key']}|{dedupe_key(addr)}",
                "brand_key": bd["key"],
                "brand": bd["name"],
                "brand_target": list(bd["brand_target"]),
                "category": bd["category"],
                "name": name,
                "addr_raw": addr,
                "addr_cands": cands,
                "sido": sido_of(addr),
                "base_ym": ym,
                "source": "xlsx",
            })
            n += 1
        per_brand[bd["name"]] = n

    # ---- 미등록 시트는 통째 누락이라 조용히 넘기면 안 된다 ----
    if unknown_sheets:
        raise SystemExit(
            f"[FATAL] brands.py 에 없는 시트: {unknown_sheets}\n"
            f"        → 브랜드를 추가하거나 aliases 에 표기를 등재할 것. "
            f"그냥 두면 이 시트가 통째로 지도에서 빠진다.")

    # ---- 같은 주소 중복 등록 제거 ----
    # 원본에 '서판교소마' / '서판교소마센터' 가 같은 주소로 두 번 들어와 있었다.
    # 안 지우면 지도에 경쟁사가 실제보다 많아 보인다.
    seen_d: dict[str, dict] = {}
    deduped = []
    for r in recs:
        prev = seen_d.get(r["dkey"])
        if prev:
            problems["중복제거"].append(f"{r['brand']}/{r['name']} ≡ {prev['name']}  ({r['addr_raw']})")
            continue
        seen_d[r["dkey"]] = r
        deduped.append(r)
    raw_brand = dict(per_brand)
    for b in per_brand:
        per_brand[b] = sum(1 for r in deduped if r["brand"] == b)
    recs = deduped

    for bd in B.BRANDS:
        nm = bd["name"]
        if nm not in per_brand:
            continue
        drop = raw_brand[nm] - per_brand[nm]
        print(f"[load] {nm:8s} {per_brand[nm]:4d}건  ({B.CATEGORIES[bd['category']]})"
              + (f"   ← 중복 {drop}건 제거" if drop else ""))

    # ---- 주소가 다른데 id 가 겹치는 것은 별개 지점이다 → 번호를 붙여 살린다 ----
    cnt = Counter(r["id"] for r in recs)
    seq: dict[str, int] = defaultdict(int)
    for r in recs:
        if cnt[r["id"]] > 1:
            seq[r["id"]] += 1
            problems["동명이지점"].append(f"{r['brand']}/{r['name']} → {r['id']}#{seq[r['id']]}")
            r["id"] = f"{r['id']}#{seq[r['id']]}"

    # ---- base_ym 혼재 ----
    ym_main = ym_seen.most_common(1)[0][0] if ym_seen else ""
    if len(ym_seen) > 1:
        print(f"[load] ⚠️ 데이터기준 혼재: {dict(ym_seen)} → 대표값 {ym_main}")

    # 원본에 '데이터기준' 컬럼이 없는 경우 (와이즈만) — 기준월을 만들어 넣는다.
    # ⚠️ 여기서 빈 값으로 두면 `manual_unknown.json` 이 되고, build 의 원천 선택이
    #    "가장 최신 base_ym" 기준이라 이 자료가 영영 안 뽑히거나 엉뚱하게 뽑힌다.
    if not ym_main:
        if a.base_ym:
            ym_main, why = a.base_ym, "--base-ym"
        else:
            ym_main = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y%m")
            why = f"원본 수정일 {datetime.fromtimestamp(src.stat().st_mtime):%Y-%m-%d}"
        print(f"[load] ⚠️ '데이터기준' 컬럼이 없다 → 기준월 {ym_main} ({why})")
        for r in recs:
            r["base_ym"] = ym_main

    print(f"\n[load] 합계 {len(recs):,}건 · 브랜드 {len(per_brand)}개 · 기준 {ym_main}")
    for k, v in problems.items():
        print(f"[load] ⚠️ {k} {len(v)}건")
        for x in v[:5]:
            print(f"         {x}")

    if a.dry_run:
        print("[load] --dry-run 이라 파일을 쓰지 않았다")
        return 0

    # ---- 원본 보존 (SSOT) ----
    MANUAL.mkdir(parents=True, exist_ok=True)
    dst = MANUAL / src.name
    if not dst.exists() or sha256(dst) != sha256(src):
        shutil.copy2(src, dst)
        print(f"[load] 원본 보존 {dst}")
    digest = sha256(dst)

    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"manual_{tag + '_' if tag else ''}{ym_main or 'unknown'}.json"
    out.write_text(json.dumps({
        "meta": {
            "sample": False,
            "updated": date.today().isoformat(),
            "base_ym": ym_main,
            "source": "관리자 수기 조사 xlsx",
            "source_file": str(dst),
            "sha256": digest,
            "counts": per_brand,
            "problems": {k: len(v) for k, v in problems.items()},
        },
        "data": recs,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"[load] 출력 {out}  ({len(recs):,}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
