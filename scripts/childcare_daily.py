# -*- coding: utf-8 -*-
"""childcare_daily.py — 어린이집 잔여 시군구 수집 → 병합 → 검증 → 발행 (무인 실행)

    python scripts/childcare_daily.py            스케줄러가 매일 부른다
    python scripts/childcare_daily.py --dry-run  발행 직전까지만
    python scripts/childcare_daily.py --no-publish

왜 스케줄러인가
    2026-08-12 전국 수집에서 시군구 10개가 빠졌다. 포털 arcode 가 행안부 표준코드와
    다른 곳이 있고(코드 문제), 멀쩡한 코드도 가끔 INFO-200 을 준다(단발 실패).
    게다가 **호출 한도에 걸리면 아는 코드까지 0건**이 되어 그날은 더 못 받는다.
    → "되는 날 알아서 마저 받는다"가 유일하게 사람 손이 안 가는 방식이다.

동작 (B안 · 축적 후 일괄 발행)
    ① 이미 다 찼으면 아무것도 안 한다 (매일 돌아도 무해)
    ② 한도가 안 풀렸으면 조용히 넘긴다 — 내일 다시
    ③ 못 받은 시군구만 재시도 → 원본(raw) 갱신
    ④ 원본 전량 재집계(--from-raw) → 지표 재조립
    ⑤ 검증 2종 통과해야만 발행. 하나라도 빠져 있으면 **발행하지 않고 기다린다**
       (0 은 결측이 아니라 실측으로 보인다 — 빈 지역을 발행하면 "어린이집 없는 상권"이 된다)

로그: data/logs/childcare_YYYYMMDD.log
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
LOGS = ROOT / "data" / "logs"
PY = sys.executable
API = "http://api.childcare.go.kr/mediate/rest/cpmsapi030/cpmsapi030/request"
PROBE_ARCODE = "26350"          # 해운대구 — 운영키면 137건 나오던 자리
LOG_LINES: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    LOG_LINES.append(msg)


def flush_log() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    p = LOGS / f"childcare_{datetime.now():%Y%m%d}.log"
    with p.open("a", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES) + "\n")


def run(*args: str) -> int:
    """자식 프로세스 실행. 출력은 그대로 흘리고 로그에도 남긴다."""
    log(f"  $ {' '.join(Path(a).name if a.endswith(('.py', '.js')) else a for a in args)}")
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        log("    " + line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines()[-15:]:
            log("    ! " + line)
    return r.returncode


def key() -> str | None:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("CHILDCARE_KEY=") and s.split("=", 1)[1].strip():
            return s.split("=", 1)[1].strip()
    return None


def quota_ok(k: str) -> bool:
    """한도 회복 여부. **한도 초과도 0건으로 오기 때문에** 아는 자리로 재 본다."""
    try:
        req = urllib.request.Request(f"{API}?key={k}&arcode={PROBE_ARCODE}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return len(re.findall(r"<item>", r.read().decode("utf-8", "replace"))) > 0
    except Exception as e:  # noqa: BLE001
        log(f"  호출 실패: {type(e).__name__}")
        return False


def coverage() -> tuple[bool, list[str]]:
    """다 찼는가. meta 만 믿지 않고 **시군구별 값**으로도 본다 —
    경계 넘어온 1~2건이 0 을 면하게 만들어 '수집됨'처럼 보이는 사례가 실제로 있었다."""
    cc = json.loads((PROCESSED / "childcare.json").read_text(encoding="utf-8"))
    met = json.loads((PROCESSED / "metrics_sgg.json").read_text(encoding="utf-8"))
    kids: dict[str, int] = {}
    for adm, v in (cc.get("data") or {}).items():
        kids[adm[:5]] = kids.get(adm[:5], 0) + (v.get("daycare_kids") or 0)
    thin = []
    for c, v in (met.get("data") or {}).items():
        pre = v.get("preschool")                 # 만0~5세 주민등록 인구
        if pre and kids.get(c, 0) < pre * 0.05:  # 유아 인구의 5% 미만이면 못 받은 것
            thin.append(f"{c} {v.get('name')}({kids.get(c, 0)}/{pre})")
    return (not thin), thin


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="어린이집 잔여 수집 → 발행 (무인)")
    ap.add_argument("--dry-run", action="store_true", help="발행 직전까지만")
    ap.add_argument("--no-publish", action="store_true", help="검증까지만 하고 발행 안 함")
    a = ap.parse_args(argv)

    log("=" * 74)
    log(f"어린이집 일일 수집 · {datetime.now():%Y-%m-%d %H:%M:%S}")
    log("=" * 74)

    try:
        # ① 이미 다 찼는가
        done, thin = coverage()
        if done:
            log("[완료] 전 시군구 커버 — 할 일 없음. (스케줄 작업을 꺼도 된다)")
            return 0
        log(f"[대상] 아직 빈 시군구 {len(thin)}개")
        for t in thin[:12]:
            log(f"   - {t}")

        # ② 한도
        k = key()
        if not k:
            log("[중단] .env 의 CHILDCARE_KEY 가 비어 있다.")
            return 1
        if not quota_ok(k):
            log("[대기] 호출 한도가 아직 안 풀렸다 (아는 코드도 0건) — 내일 다시 시도한다.")
            return 0
        log("[한도] 회복 확인 — 수집을 진행한다.")

        # ③ 못 받은 곳만 재시도 (원본만 갱신된다)
        if run(PY, "scripts/collect_childcare.py", "--retry-missing") != 0:
            log("[중단] 재시도 수집이 실패했다.")
            return 1

        # ④ 원본 전량 재집계 → 지표
        if run(PY, "scripts/collect_childcare.py", "--from-raw") != 0:
            log("[중단] 재집계 실패.")
            return 1
        if run(PY, "scripts/build_metrics.py") != 0:
            log("[중단] 지표 조립 실패.")
            return 1

        done, thin = coverage()
        if not done:
            log(f"[보류] 아직 {len(thin)}개가 비어 있다 — **발행하지 않는다.**")
            for t in thin[:12]:
                log(f"   - {t}")
            log("   코드 불일치로 굳어졌다면 포털 화면에서 해당 지역 arcode 를 확인해")
            log("   scripts/collect_childcare.py 의 ARCODE_OVERRIDE 에 넣을 것.")
            return 0
        log("[충족] 전 시군구가 채워졌다.")

        # ⑤ 발행 (publish.bat 이 검증 2종을 먼저 돌린다 — 그게 발행 게이트다)
        if a.dry_run or a.no_publish:
            log("[중지] --dry-run/--no-publish 라 발행하지 않았다.")
            return 0
        rc = subprocess.run(["cmd", "/c", str(ROOT / "publish.bat")], cwd=ROOT,
                            capture_output=True, text=True, encoding="cp949",
                            errors="replace")
        for line in (rc.stdout or "").splitlines():
            log("    " + line)
        if rc.returncode != 0:
            log("[실패] 검증 또는 발행에서 멈췄다. 위 로그를 볼 것.")
            return 1
        log("[발행] 완료 — https://jichang999-dotcom.github.io/tes2/jiyeok/web/")
        return 0
    finally:
        flush_log()


if __name__ == "__main__":
    sys.exit(main())
