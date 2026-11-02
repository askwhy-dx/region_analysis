# -*- coding: utf-8 -*-
"""
E-4 심판 — 경계 폴리곤 ↔ 통계청 행정동이 갈릴 때 **행안부 주소(JUSO)** 로 정답을 가린다.

배경
----
`geocode_addr.py` 는 두 경로로 행정동을 뽑는다.
  경로B(PIP)  : SGIS 가 준 좌표를 admdongkor 경계 폴리곤(ver20260701)에 찍는다  ← 지금까지 정본
  경로A(브리지): SGIS 가 준 통계청 8자리를 code_bridge 로 행안부 코드로 바꾼다   ← 교차검증용
둘이 갈리면 `xcheck='mismatch'` 로 기록만 하고 **PIP 를 썼다.** 그런데 실측해 보니
**PIP 가 항상 옳지는 않다.**

  전주 만성지구 : 주민등록은 혁신동인데 폴리곤은 아직 여의동  → PIP 오답 (아파트 거주 비율 188.5%)
  전주 송천3동   : 통계청 쪽이 송천1동으로 잘못 봄            → SGIS 오답

즉 **어느 한쪽을 일괄 채택하는 규칙은 둘 다 틀린다.** 실측으로 확인했다 —
SGIS 를 일괄 채택하면 여의동(1.885)은 고쳐지지만 송천1동이 1.550 으로 새로 깨졌다.

그래서 제3의 심판을 쓴다. **도로명주소의 행정동은 행안부가 정의한다** — 폴리곤도
통계청 분류도 그 파생물이다. JUSO(business.juso.go.kr)가 그 원본이다.

실측 판정 (분쟁 1,300건, 2026-08-05)
  SGIS 정답 1,118 · PIP 정답 134 · 미해결 32 · 제3의 동 1

⚠️ 함정 (전부 실측)
----------------
- **JUSO 의 `admCd` 는 법정동코드다. 행정동코드가 아니다.** 둘 다 10자리 숫자라
  잘못 조인해도 에러가 안 나고 틀린 동에 값이 얹힌 채로 지나간다. 행정동은
  `hemdNm`(**이름만**) 으로 오므로 `code_map` 이름 매칭이 유일한 경로다.
- **`addInfoYn=Y` 를 빼면 `hemdNm` 이 아예 안 온다.** 에러가 아니라 필드가 없다.
- **이름 정규화 2종이 없으면 절반이 날아간다** — `용호제2동`→`용호2동`(숫자 앞 '제'),
  `고양시 덕양구`→`고양시덕양구`(공백). 실측 무보정 8/15 → 정규화 후 30/30.
- **`xcheck='absent'` 는 대체로 이 문제가 아니다.** 표본 276건 판정 결과 PIP 가 옳다
  (전남광주통합은 SGIS 쪽이 2025 기준이라 브리지가 비는 게 정상). 'PIP 오답'
  으로 잡힌 소수는 `전남광주통합특별시 무안군 7` 같은 **도로명이 없는 불량 주소**라
  JUSO 가 아무거나 물어온 것이다 → 기본 범위(mismatch)에서는 건드리지 않는다.
  ⚠️ **다만 사각은 있다.** 브리지가 비면 갈릴 기회 자체가 없어 폴리곤이 낡아도
  mismatch 로 안 잡힌다. 실측으로 그런 건이 7건(약 7,300세대) 나왔다 → `--scope suspect`.
- **유니버스 밖 코드는 채택하지 않는다.** JUSO 가 우리 경계에 없는 동을 말하면
  붙일 폴리곤이 없다. 그 경우 PIP 를 유지한다(옮길 곳이 없는데 옮기면 값이 증발한다).

사용
----
  python scripts/arbitrate_adm.py                    # 분쟁분(mismatch)만 — 기본·이어받기
  python scripts/arbitrate_adm.py --scope suspect    # + 이상 동 소재 전건 (사각 보정)
  python scripts/arbitrate_adm.py --scope all        # 캐시 전건 (약 10만건 · 수 시간)
  python scripts/arbitrate_adm.py --dry-run          # 판정만 (지오코딩 캐시 안 건드림)

교정 뒤에는 **캐시를 읽는 모든 산출물을 다시 만들어야 한다**
(build_kapt → build_poi → build_metrics · collect_neis/collect_rtms 재집계).
`update_adm_fix.bat` 이 그 순서를 묶어 둔다.

⚠️ **`collect_rtms.py --no-download` 를 빼먹지 말 것** (2026-08-12 실측).
   아파트 단지 목록(`rtms_complex.json`)에 `adm_cd` 가 **박제**돼 있어서, 캐시만 고치고
   build_poi 를 돌리면 단지 101건이 옛 동에 남는다. build_poi 는 저장된 값을 읽을 뿐이라
   조용히 지나간다 — verify_c8 의 좌표 정합 검사가 이걸 잡는다.

⚠️ 학교는 이 캐시를 안 쓴다. `load_school.py` 가 같은 심판(`adm_arbiter`)을 **따로**
   호출한다 — 원천 CSV 의 좌표를 직접 PIP 하기 때문이다. 그래서 이 스크립트를 돌린
   뒤에는 `load_school.py` 도 다시 돌려야 한다(교정 이력에서 낡은 동 목록을 받아 간다).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
CACHE = PROC / "geocode_cache.json"
VERDICT = PROC / "juso_arbiter.json"
API = "https://business.juso.go.kr/addrlink/addrLinkApi.do"


# 심판 로직 본체는 adm_arbiter 가 갖는다 — 학교 로더와 같은 규칙을 써야 하므로
# 여기에 사본을 두지 않는다 (규칙이 갈리면 같은 주소가 축마다 다른 동에 떨어진다).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from adm_arbiter import Juso, NameIndex, RoadIndex, read_key, norm   # noqa: E402,F401


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="JUSO 판정 캐시 무시하고 재조회")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-outlier-guard", action="store_true",
                    help="외톨이 판정 가드를 끈다 (2026-08-12 이전 동작)")
    ap.add_argument("--revert-outliers", action="store_true",
                    help="이미 반영된 심판 교정 중 **외톨이만** 좌표 PIP 로 되돌린다")
    ap.add_argument("--restore-apt-guard", action="store_true",
                    help="되돌린 것 중 아파트 거주비율 100%% 초과를 만든 건만 JUSO 판정으로 복원")
    ap.add_argument("--scope", default="mismatch",
                    choices=("mismatch", "suspect", "absent", "all"),
                    help="mismatch=분쟁분만(기본) · suspect=분쟁분+이상 동 소재 전건 · "
                         "absent=분쟁분+브리지 결측(사각지대, 약 1.2만건) · "
                         "all=캐시 전건(약 10만건, 수 시간)")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cache_doc = json.loads(CACHE.read_text(encoding="utf-8"))
    cache = cache_doc["data"]
    cmap = json.loads((PROC / "code_map.json").read_text(encoding="utf-8"))
    idx = NameIndex(cmap)

    # ---- 되돌리기의 안전장치 (2026-08-12) --------------------------------
    # 이웃 다수결만으로는 부족하다. 실측: 상현1동으로 6건을 되돌렸더니 **아파트 세대수가
    # 주민등록 세대수의 126.7%** 가 됐다 — 물리적으로 불가능하니 그 되돌림이 틀린 것이다
    # (상현3동은 분동 신설이라 JUSO 가 옳았다). 이웃이 PIP 편이어도 **물리 정합성이 깨지면
    # 되돌리지 않는다.** 순서는 revert → 재빌드 → 이 모드다.
    if a.restore_apt_guard:
        met = json.loads((PROC / "metrics.json").read_text(encoding="utf-8"))["data"]
        sus = {c for c, v in met.items() if v.get("apt_ratio_suspect")}
        nm_adm = {r["code"]: r["name"] for r in cmap["adm"]}
        fixed = 0
        for addr, r in cache.items():
            if not r or r.get("arbiter") != "pip_outlier" or r.get("adm_cd") not in sus:
                continue
            cd, _ = idx.resolve(r.get("juso_hemd") or "", r.get("adm_cd"))
            if not cd or cd not in idx.universe:
                continue
            print(f"    {addr}")
            print(f"       {nm_adm.get(r['adm_cd'])} → {nm_adm.get(cd)} (거주비율 가드)")
            r["adm_cd"] = cd
            r["arbiter"] = "juso"
            r["outlier_override"] = "apt_ratio_guard"
            r.pop("outlier_peers", None)
            r.pop("outlier_peer_top", None)
            fixed += 1
        print(f"[guard] 아파트 거주비율 100% 초과를 만든 되돌림 {fixed}건을 JUSO 판정으로 복원")
        if a.dry_run:
            print("[guard] --dry-run 이라 캐시는 건드리지 않았다")
            return 0
        cache_doc.setdefault("meta", {}).setdefault("outlier_guard", {})["apt_ratio_restored"] = fixed
        CACHE.write_text(json.dumps(cache_doc, ensure_ascii=False), encoding="utf-8")
        print("⚠️ 산출물을 다시 만들 것 (build_kapt → build_poi → build_metrics)")
        return 0

    # ---- 되돌리기 모드 ----------------------------------------------------
    # 이미 캐시에 반영된 심판 교정 중 **외톨이만** 좌표 PIP 로 되돌린다.
    # 감사 리포트를 믿지 않고 여기서 다시 판정한다 — 판정 로직이 한 곳(RoadIndex)에만
    # 있어야 나중에 규칙을 고쳤을 때 결과가 갈리지 않는다.
    if a.revert_outliers:
        nm_adm = {r["code"]: r["name"] for r in cmap["adm"]}
        ri = RoadIndex()
        applied = [(addr, r) for addr, r in cache.items()
                   if r and r.get("arbiter") == "juso"
                   and r.get("adm_cd_pip") and r["adm_cd_pip"] != r.get("adm_cd")]
        print(f"[revert] 심판이 옮긴 채로 남아 있는 건 {len(applied):,}건 — 이웃 대조 시작")
        rev, kept, unknown = [], 0, 0
        for i, (addr, r) in enumerate(applied, 1):
            pip = r["adm_cd_pip"]
            is_out, peers, top = ri.is_outlier(addr, r.get("juso_hemd") or "", nm_adm.get(pip, ""))
            if is_out:
                rev.append((addr, r.get("adm_cd"), pip, peers, top))
            elif peers:
                kept += 1
            else:
                unknown += 1
            if i % 200 == 0:
                print(f"    ... {i:,}/{len(applied):,} · 되돌릴 것 {len(rev)}")
        ri.save()
        print(f"[revert] 되돌릴 {len(rev)}건 · 유지 {kept}건 · 표본없음 {unknown}건")
        for addr, cur, pip, peers, top in sorted(rev, key=lambda x: -x[3])[:10]:
            print(f"    {addr}")
            print(f"       {nm_adm.get(cur)} → {nm_adm.get(pip)} (이웃 {peers}건 '{top}')")
        if a.dry_run:
            print("[revert] --dry-run 이라 캐시는 건드리지 않았다")
            return 0
        for addr, cur, pip, peers, top in rev:
            e = cache[addr]
            e["adm_cd"] = pip
            e["arbiter"] = "pip_outlier"          # 되돌렸다는 사실을 남긴다
            e["outlier_peers"] = peers
            e["outlier_peer_top"] = top
        cache_doc.setdefault("meta", {})["outlier_guard"] = {
            "updated": date.today().isoformat(),
            "reverted": len(rev), "kept": kept, "no_sample": unknown,
            "note": "JUSO 원본의 외톨이 판정을 좌표 PIP 로 되돌림 (E-4 보강)"}
        CACHE.write_text(json.dumps(cache_doc, ensure_ascii=False), encoding="utf-8")
        print(f"[revert] 캐시 갱신 완료 — {len(rev)}건 되돌림")
        print("⚠️ 캐시를 읽는 산출물을 전부 다시 만들 것 "
              "(build_competitor → build_kapt → build_poi → build_metrics)")
        return 0

    verdicts: dict = {}
    if VERDICT.exists() and not a.refresh:
        verdicts = json.loads(VERDICT.read_text(encoding="utf-8")).get("data", {})

    # ---- 심판 대상 고르기 ----
    #
    # 기본은 `mismatch` 다 — 두 경로가 실제로 갈린 곳이라 분쟁이 명시적이다.
    # 그런데 **mismatch 만으로는 다 못 잡는다.** 전남광주통합처럼 브리지가 통째로
    # 비는(=absent) 지역은 갈릴 기회 자체가 없어서, 폴리곤이 낡아도 조용히 지나간다.
    # 실측: 이상 동(아파트 거주 비율 100% 초과) 소재 단지 634건을 전수 조회하니
    # mismatch 가 아닌 곳에서 7건(약 7,300세대)이 더 나왔다.
    # → `suspect` 는 그 사각을 메우고, `all` 은 캐시 전건을 JUSO 기준으로 다시 세운다.
    def is_suspect_zone() -> set[str]:
        p = PROC / "metrics.json"
        if not p.exists():
            return set()
        d = json.loads(p.read_text(encoding="utf-8"))["data"]
        return {c for c, r in d.items() if r.get("apt_ratio_suspect")}

    if a.scope == "all":
        disputes = [(addr, r) for addr, r in cache.items() if r]
    elif a.scope == "absent":
        # 사각지대만 정조준한다. `all` 은 10만건인데 그중 9.3만건은 두 경로가 이미
        # 일치(match)해서 물어볼 이유가 없다 — 갈릴 기회조차 없었던 `absent` 만
        # 보면 **10분의 1 비용으로 같은 사각**을 덮는다.
        # ⚠️ 여기서 나오는 '교정'은 대부분 없을 것이다(표본 276건에서 PIP 가 옳았다).
        #    수확이 적은 게 정상이고, 그걸 확인하는 것 자체가 이 실행의 목적이다.
        disputes = [(addr, r) for addr, r in cache.items()
                    if r and r.get("xcheck") in ("mismatch", "absent")]
    elif a.scope == "suspect":
        zone = is_suspect_zone()
        disputes = [(addr, r) for addr, r in cache.items()
                    if r and (r.get("xcheck") == "mismatch" or r.get("adm_cd") in zone)]
    else:
        disputes = [(addr, r) for addr, r in cache.items()
                    if r and r.get("xcheck") == "mismatch"]
    todo = [d for d in disputes if d[0] not in verdicts]
    print(f"[arbit] 분쟁 {len(disputes):,}건 · 신규 조회 {len(todo):,}건 "
          f"· 판정캐시 {len(disputes) - len(todo):,}건")

    if todo:
        j = Juso(read_key(), use_cache=False)   # 판정 캐시는 이 함수가 직접 관리한다
        lock = threading.Lock()
        done = [0]

        def work(item):
            addr, r = item
            hemd = j.hemd(addr)
            with lock:
                done[0] += 1
                if done[0] % 200 == 0:
                    print(f"    ... {done[0]:,}/{len(todo):,}", flush=True)
                verdicts[addr] = {"hemd": hemd, "pip": r.get("adm_cd")}

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(work, todo))

    # ---- 판정 → 교정 ----
    tally = Counter()
    changes: list[tuple[str, str, str, str]] = []
    held: list[tuple] = []
    nm_adm = {r["code"]: r["name"] for r in cmap["adm"]}
    road_idx = None if a.no_outlier_guard else RoadIndex()
    for addr, r in disputes:
        v = verdicts.get(addr) or {}
        hemd = v.get("hemd")
        pip = r.get("adm_cd")
        if not hemd:
            tally["조회실패"] += 1
            continue
        cd, how = idx.resolve(hemd, pip)
        if not cd:
            tally["이름매칭실패"] += 1
            continue
        if cd not in idx.universe:          # 붙일 폴리곤이 없다 → 옮기지 않는다
            tally["유니버스밖"] += 1
            continue
        if cd == pip:
            tally["PIP정답"] += 1
            continue
        # ---- 외톨이 가드 (2026-08-12) --------------------------------------
        # 행안부 원본에도 한 번지만 틀린 레코드가 있다. 이웃 번지에 되물어
        # **JUSO 자신이 소수의견이면 채택하지 않는다.** 상세는 adm_arbiter.RoadIndex
        if road_idx is not None:
            is_out, peers, top = road_idx.is_outlier(addr, hemd, nm_adm.get(pip, ""))
            if is_out:
                tally["외톨이보류"] += 1
                held.append((addr, pip, cd, hemd, peers, top))
                continue
        tally["교정"] += 1
        changes.append((addr, pip, cd, hemd))

    print(f"[arbit] 판정 — {dict(tally)}")
    if held:
        print(f"[arbit] 외톨이라 보류 {len(held)}건 (이웃 근거 많은 순 5)")
        for addr, pip, cd, hemd, peers, top in sorted(held, key=lambda x: -x[4])[:5]:
            print(f"    {addr} · JUSO '{hemd.split()[-1]}' vs 이웃 {peers}건 '{top}' → PIP 유지")
    if road_idx is not None:
        road_idx.save()
    if changes:
        pairs = Counter((p, c) for _, p, c, _ in changes)
        nm = {r["code"]: r["name"] for r in cmap["adm"]}
        print(f"[arbit] 교정 {len(changes):,}건 · 구간 {len(pairs)}종 (상위 10)")
        for (p, c), n in pairs.most_common(10):
            print(f"    {n:5}  {p} {nm.get(p)} → {c} {nm.get(c)}")

    # 판정 캐시는 dry-run 에서도 남긴다 — 조회 결과지 산출물이 아니고,
    # 안 남기면 본실행이 1,300건을 다시 물어 JUSO 한도만 태운다.
    VERDICT.write_text(json.dumps(
        {"meta": {"updated": date.today().isoformat(), "source": "JUSO addrLinkApi",
                  "count": len(verdicts), "tally": dict(tally)},
         "data": verdicts}, ensure_ascii=False), encoding="utf-8")

    if a.dry_run:
        print("[arbit] --dry-run 이라 지오코딩 캐시는 건드리지 않았다 (판정 캐시만 저장)")
        return 0

    for addr, pip, cd, hemd in changes:
        e = cache[addr]
        e.setdefault("adm_cd_pip", pip)     # 원값 보존 — 되돌릴 수 있게
        e["adm_cd"] = cd
        e["arbiter"] = "juso"
        e["juso_hemd"] = hemd

    VERDICT.write_text(json.dumps(
        {"meta": {"updated": date.today().isoformat(), "source": "JUSO addrLinkApi",
                  "count": len(verdicts), "tally": dict(tally)},
         "data": verdicts}, ensure_ascii=False), encoding="utf-8")

    cache_doc.setdefault("meta", {})["arbiter"] = {
        "updated": date.today().isoformat(), "source": "JUSO addrLinkApi (E-4)",
        "corrected": len(changes), "tally": dict(tally)}
    CACHE.write_text(json.dumps(cache_doc, ensure_ascii=False), encoding="utf-8")
    print(f"[arbit] 캐시 갱신 완료 — {len(changes):,}건 교정")
    print("⚠️ 캐시를 읽는 산출물을 전부 다시 만들 것 "
          "(build_kapt → build_poi → build_metrics · 학원/아파트 재집계)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
