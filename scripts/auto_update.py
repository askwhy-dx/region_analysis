# -*- coding: utf-8 -*-
"""auto_update.py — 월간점검 직후 자동 갱신 (2026-11-03 관리자 지시)

monthly_check.bat 이 점검 직후 부른다. 점검 결과(data/logs/monthly_check_result.json)를 읽어
**데이터별로** 갱신하거나 보류하고, 결과를 슬랙 한 통으로 보낸다.

주기 (관리자 확정 2026-11-03)
  월 1회   자사·경쟁사·시너지세권
  분기 1회 인구 · 아파트 실거래 · 아파트 세대수 · 어린이집 · 학원   (1·4·7·10월)
  자동 안 함  경계 새 버전(관리자 컨펌 사항, DATA_CONTRACT §0-9) · 학교(연 1회 CSV 수동) → 슬랙 안내만

판정
  · 점검의 데이터·화면 검증이 FAIL  → 이번 회차 **전체 보류** (기준선이 깨진 채 덧칠하지 않는다)
  · 그 데이터에 걸린 점검 항목이 FAIL → **그 데이터만 보류**
  · 수집·검증 단계가 실패하면 그 데이터의 산출물을 **갱신 전으로 되돌리고** 보류
  · 마지막 검증(verify_c8·verify_dashboard)이 깨지면 **전부 되돌리고 발행 안 함**
  · K-apt 는 일일 한도(10,000건)로 22,257단지를 사흘에 걸쳐 받는다 → 다 받을 때까지
    평일마다 `--resume` 으로 이어받고, 다 받으면 그때 집계·검증·발행한다

사용
  python scripts/auto_update.py              # 점검 직후 (오늘 날짜 점검 결과 필요)
  python scripts/auto_update.py --resume     # 이어받을 K-apt 가 있으면 진행 (스케줄러가 평일 매일)
  python scripts/auto_update.py --quarter    # 분기 달이 아니어도 분기 항목까지 갱신 (수동)
  python scripts/auto_update.py --dry-run    # 계획만 출력, 아무것도 안 바꿈
  python scripts/auto_update.py --no-slack
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")   # cp949 콘솔에 맞춘다 (monthly_check.py 주석 참조)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
LOG_DIR = ROOT / "data" / "logs"
RESULT_F = LOG_DIR / "monthly_check_result.json"
STATE_F = LOG_DIR / "auto_update_state.json"
PY = sys.executable
QUARTER_MONTHS = (1, 4, 7, 10)
STEP_TIMEOUT = 3 * 3600

# 항목 정의 — fail: 이 점검 항목이 FAIL 이면 그 데이터만 보류 / newer: 월간점검 「원천 최신월」 라벨
ITEMS = {
    "competitor": {"name": "자사·경쟁사·시너지세권", "cycle": "month", "fail": ["자사·경쟁사 위치"],
                   "meta": "competitor_adm.json"},
    "population": {"name": "인구", "cycle": "quarter", "fail": ["인구 기준월", "원천 최신월(인구)"],
                   "newer": "인구", "meta": "population.json"},
    "rtms": {"name": "아파트 실거래", "cycle": "quarter", "fail": ["아파트 실거래"],
             "newer": "아파트 실거래", "meta": "apartment.json"},
    "childcare": {"name": "어린이집 원아", "cycle": "quarter",
                  "fail": ["어린이집 원아", "어린이집 부분수집", "어린이집 코드표"],
                  "newer": "어린이집 원아", "meta": "childcare.json"},
    "academy": {"name": "학원", "cycle": "quarter", "fail": [], "meta": "academy.json"},
    "kapt": {"name": "아파트 세대수", "cycle": "quarter", "fail": ["아파트 세대수"],
             "newer": "아파트 세대수", "meta": "kapt.json"},
}
GLOBAL_FAIL = ("데이터 검증", "화면 검증")

LOG: list[str] = []


def say(msg: str = "") -> None:
    print(msg)
    LOG.append(msg)


def basis(key: str) -> str:
    """화면에 보이는 기준 — 기준월, 없으면 수집일."""
    try:
        m = json.loads((PROC / ITEMS[key]["meta"]).read_text(encoding="utf-8")).get("meta", {})
    except Exception:  # noqa: BLE001
        return "?"
    if m.get("period"):
        return m["period"]
    return m.get("base_ym") or m.get("collected_at") or m.get("updated") or "?"


def run(label: str, cmd: list[str]) -> tuple[int, str]:
    say(f"  $ {' '.join(Path(c).name if i < 2 else c for i, c in enumerate(cmd))}")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, timeout=STEP_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"({label} {STEP_TIMEOUT // 3600}시간 초과)"
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    LOG.append(out[-6000:])
    return p.returncode, out


def last_line(out: str) -> str:
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    for l in reversed(lines):
        if "판정" in l or "중단" in l or "FATAL" in l or "Error" in l:
            return l[:200]
    return (lines[-1] if lines else "")[:200]


# ------------------------------------------------------------------ 되돌리기

def snapshot() -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in PROC.rglob("*") if p.is_file()}


def restore(before: dict[str, float], backup: Path) -> int:
    """before 이후 바뀐·새로 생긴 산출물을 백업본으로 되돌린다."""
    n = 0
    for p in PROC.rglob("*"):
        if not p.is_file():
            continue
        k = str(p)
        if k in before and p.stat().st_mtime == before[k]:
            continue
        src = backup / p.relative_to(PROC)
        if src.is_file():
            shutil.copy2(src, p)
        else:
            p.unlink()
        n += 1
    return n


# ------------------------------------------------------------------ 항목별 갱신

class Hold(Exception):
    """이 데이터는 보류 — 메시지가 슬랙에 그대로 나간다."""


class Pending(Exception):
    """수집이 덜 끝났다 (K-apt) — 다음 평일 이어받는다."""


def _prev_ym(today: date, n: int) -> str:
    t = today.year * 12 + today.month - 1 - n
    return f"{t // 12:04d}{t % 12 + 1:02d}"


def do_competitor(notes: list[str]) -> None:
    rc, out = run("크롤", [PY, "scripts/crawl_competitor.py", "--delay", "0.5"])
    if rc >= 2:
        raise Hold(f"크롤 전체 실패 — {last_line(out)}")
    if rc == 1:
        notes.append("일부 브랜드 크롤 실패 → 그 브랜드는 직전 회차 유지")
    for preset in ("waikids", "wiseman"):
        rc, out = run("자사", [PY, "scripts/load_competitor.py", "--preset", preset])
        if rc:
            notes.append(f"자사 xlsx({preset}) 로드 실패 → 직전 회차 유지")
    rc, out = run("집계", [PY, "scripts/build_competitor.py"])
    if rc:
        raise Hold(f"build_competitor 하드가드 중단 — {last_line(out)}")


POP_YM: list = [None]   # 월간점검이 확인한 행안부 최신 공표월


def do_population(notes: list[str]) -> None:
    rc, out = run("인구", [PY, "scripts/collect_population.py"] + (["--ym", POP_YM[0]] if POP_YM[0] else []))
    if rc:
        raise Hold(f"collect_population 실패 — {last_line(out)}")
    rc, out = run("인구검증", [PY, "scripts/verify_population.py", "--strict"])
    if rc:
        raise Hold(f"인구 교차검증 불일치 — {last_line(out)} (행정구역 변환 컨펌 필요할 수 있음)")


def do_rtms(notes: list[str]) -> None:
    today = date.today()
    # 최근 3개월은 신고기한 때문에 미확정 → 다시 받는다 (docs/RTMS_AUTO.md)
    rc, out = run("실거래(최근3개월)", [PY, "scripts/collect_rtms.py", "--from", _prev_ym(today, 3),
                                    "--to", _prev_ym(today, 1), "--download-only", "--force"])
    if rc:
        raise Hold(f"최근 3개월 재다운로드 실패 — {last_line(out)}")
    rc, out = run("실거래", [PY, "scripts/collect_rtms.py"])
    if rc:
        raise Hold(f"collect_rtms 실패 — {last_line(out)}")


def do_childcare(notes: list[str]) -> None:
    rc, out = run("어린이집", [PY, "scripts/collect_childcare.py"])
    if rc:
        raise Hold(f"collect_childcare 실패(rc={rc}) — {last_line(out)}")
    m = json.loads((PROC / "childcare.json").read_text(encoding="utf-8")).get("meta", {})
    if m.get("partial"):
        raise Hold("부분수집(빈 시군구 있음) — 화면에서 0개로 보이므로 반영 안 함")


def do_academy(notes: list[str]) -> None:
    rc, out = run("학원", [PY, "scripts/collect_neis.py"])
    if rc:
        raise Hold(f"collect_neis 실패 — {last_line(out)}")


def do_kapt(notes: list[str], ym: str) -> None:
    rc, out = run("세대수", [PY, "scripts/collect_kapt.py", "--ym", ym])
    m = re.findall(r"진행률\s*([\d,]+)/([\d,]+)\s*\(([\d.]+)%\)", out)
    if not m:
        raise Hold(f"collect_kapt 실패 — {last_line(out)}")
    pct = float(m[-1][2])
    if pct < 98:
        raise Pending(f"{ym} 수집 {pct:.0f}% — 일일 한도로 평일마다 이어받음")
    rc, out = run("세대수 집계", [PY, "scripts/build_kapt.py", "--ym", ym])
    if rc:
        raise Hold(f"build_kapt 실패 — {last_line(out)}")


# ------------------------------------------------------------------ 마무리(빌드·검증·발행)

def finish(before_all: dict[str, float], backup: Path) -> tuple[bool, str]:
    for label, cmd in (("POI", [PY, "scripts/build_poi.py"]),
                       ("지표", [PY, "scripts/build_metrics.py"]),
                       ("데이터 검증", [PY, "scripts/verify_c8.py"]),
                       ("화면 검증", ["node", "scripts/verify_dashboard.js"])):
        if label == "화면 검증":
            rc0, out0 = run("번들", [PY, "scripts/build_web_payload.py"])
            if rc0:
                n = restore(before_all, backup)
                return False, f"웹 번들 생성 실패 → 전부 되돌림({n}개 파일)"
        rc, out = run(label, cmd)
        if rc:
            n = restore(before_all, backup)
            return False, f"{label} 실패 — {last_line(out)} → 전부 되돌림({n}개 파일) · 발행 안 함"
    rc, out = run("발행", [PY, "scripts/deploy_pages.py"])
    if rc:
        return False, f"발행 실패 — {last_line(out)} (산출물은 갱신됨 · publish.bat 재실행 필요)"
    return True, "변경 없음" if "바뀐 게 없다" in out else "발행 완료 (Pages 반영 40~60초)"


# ------------------------------------------------------------------ 슬랙

def slack(text: str) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from monthly_check import SLACK_CHANNEL  # noqa: PLC0415
    env = {}
    try:
        for line in (ROOT.parent / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    token = env.get("SLACK_BOT_TOKEN")
    if not token:
        say("[slack] SLACK_BOT_TOKEN 없음 — 건너뜀")
        return
    import urllib.request
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": SLACK_CHANNEL, "text": text}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        say(f"[slack] ok={res.get('ok')}" + ("" if res.get("ok") else f" err={res.get('error')}"))
    except Exception as e:  # noqa: BLE001
        say(f"[slack] 전송 실패: {e}")


# ------------------------------------------------------------------ 본체

def load_state() -> dict:
    try:
        return json.loads(STATE_F.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_F.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def plan(today: date, res: dict, force_quarter: bool) -> tuple[list[str], list[str], list[str], list[str]]:
    """(갱신할 것, 보류 사유, 최신이라 건너뜀, 사람 확인 필요) — 보류는 '이름 — 사유'."""
    fails = {n: d for st, n, d in res["result"] if st == "FAIL"}
    todos = {n: d for st, n, d in res["result"] if st == "TODO"}
    newer = res.get("newer", {})
    quarter = today.month in QUARTER_MONTHS or force_quarter
    go, hold, fresh, human = [], [], [], []

    if "경계 버전" in todos:
        human.append(f"경계 새 버전: {todos['경계 버전'].split('->')[0].strip()} — 관리자 컨펌 후 수동 (자동 갱신 안 함)")
    if "학교 기준연도" in todos:
        human.append(f"학교: {todos['학교 기준연도']}")
    if "낡은 경계 의심" in todos:
        human.append(f"낡은 경계 의심: {todos['낡은 경계 의심']}")

    gfail = [g for g in GLOBAL_FAIL if g in fails]
    for key, it in ITEMS.items():
        if it["cycle"] == "quarter" and not quarter:
            continue
        if gfail:
            hold.append(f"{it['name']} — 점검 {'·'.join(gfail)} FAIL (기준선이 깨져 전체 보류)")
            continue
        bad = [f"{n}: {fails[n]}" for n in it["fail"] if n in fails]
        if bad:
            hold.append(f"{it['name']} — 점검 FAIL · {bad[0]}"[:300])
            continue
        if "newer" in it and it["newer"] not in newer:
            fresh.append(it["name"])
            continue
        go.append(key)
    # 어느 데이터에도 안 걸리는 점검 FAIL 은 따로 알린다
    mapped = set(GLOBAL_FAIL) | {n for it in ITEMS.values() for n in it["fail"]}
    for n, d in fails.items():
        if n not in mapped:
            human.append(f"점검 FAIL(갱신과 무관): {n} — {d}"[:300])
    return go, hold, fresh, human


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--quarter", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-slack", action="store_true")
    a = ap.parse_args()

    today = date.today()
    st = load_state()
    done, held, pending, notes_all = [], [], [], []
    human: list[str] = []
    fresh: list[str] = []
    title = f"🗺️ *지역분석 월간점검·자동갱신 {today:%Y-%m-%d}*"

    if a.resume:
        ym = st.get("kapt_pending")
        if not ym:
            print("[auto] 이어받을 작업 없음")
            return 0
        go = ["kapt"]
        title = f"🗺️ *지역분석 세대수 이어받기 {today:%Y-%m-%d}*"
    else:
        try:
            res = json.loads(RESULT_F.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            say("[auto] 점검 결과 파일이 없다 — 갱신 안 함")
            return 1
        if res.get("date") != f"{today:%Y-%m-%d}" or res.get("no_net") or res.get("quick"):
            say(f"[auto] 오늘 전체 점검 결과가 아니다({res.get('date')}) — 갱신 안 함")
            return 1
        go, hold, fresh, human = plan(today, res, a.quarter)
        held += hold
        ym = st.get("kapt_pending") or f"{today:%Y%m}"   # 진행 중인 회차가 있으면 그걸 잇는다
        POP_YM[0] = (res.get("newer", {}).get("인구") or [None, None])[1]

    say(title)
    say(f"[auto] 갱신 대상: {[ITEMS[k]['name'] for k in go] or '없음'}")
    for h in held:
        say(f"[auto] 보류: {h}")
    if a.dry_run:
        for h in human:
            say(f"[auto] 확인 필요: {h}")
        say("[auto] --dry-run — 아무것도 바꾸지 않았다")
        return 0

    backup = ROOT / "data" / "archive" / f"{datetime.now():%Y%m%d_%H%M}_pre_auto_update" / "processed"
    updated = []
    if go:
        shutil.copytree(PROC, backup)
        say(f"[auto] 백업 {backup.parent.relative_to(ROOT)}")
        before_all = snapshot()
        for key in go:
            it = ITEMS[key]
            old = basis(key)
            notes: list[str] = []
            before = snapshot()
            say(f"\n== {it['name']} ==")
            try:
                if key == "kapt":
                    do_kapt(notes, ym)
                    st.pop("kapt_pending", None)
                else:
                    globals()[f"do_{key}"](notes)
                new = basis(key)
                done.append(f"{it['name']}: {old} → {new}" + (f" ({'; '.join(notes)})" if notes else ""))
                updated.append(key)
            except Pending as e:
                restore(before, backup)
                st["kapt_pending"] = ym
                pending.append(f"{it['name']} — {e}")
            except Hold as e:
                n = restore(before, backup)
                held.append(f"{it['name']} — {e} → 되돌림({n}개 파일)")
            except Exception as e:  # noqa: BLE001
                n = restore(before, backup)
                held.append(f"{it['name']} — 예외 {type(e).__name__}: {e} → 되돌림({n}개 파일)"[:300])
                LOG.append(traceback.format_exc())
        save_state(st)

    publish = ""
    if go and not updated:
        shutil.rmtree(backup.parent, ignore_errors=True)   # 바뀐 게 없으면 백업도 필요 없다
    if updated:
        say("\n== 집계·검증·발행 ==")
        ok, publish = finish(before_all, backup)
        if not ok:
            held += [f"{x} — 마지막 검증에서 되돌림" for x in done]
            done = []
            if "kapt" in updated:
                st["kapt_pending"] = ym  # 받은 원본은 남아 있으니 다음 회차에 집계만 다시
                save_state(st)

    if a.resume and not updated and not held and pending:
        print("[auto] 세대수 이어받는 중 — 완료 전이라 슬랙 생략")
        _write_log(today)
        return 0

    parts = [title]
    if not a.resume and today.month not in QUARTER_MONTHS and not a.quarter:
        parts.append("_이번 달은 월 갱신(자사·경쟁사)만 — 분기 갱신은 1·4·7·10월_")
    if done:
        parts += ["✅ *갱신*"] + [f"• {x}" for x in done]
    if publish:
        parts.append(f"🚀 {publish}")
    if held:
        parts += ["⏸️ *보류 (이상 있는 데이터)*"] + [f"• {x}" for x in held]
    if pending:
        parts += ["⏳ *진행 중*"] + [f"• {x}" for x in pending]
    if fresh:
        parts.append(f"➖ 원천에 새 자료 없음: {', '.join(fresh)}")
    if human:
        parts += ["📌 *사람 확인 필요*"] + [f"• {x}" for x in human]
    if not (done or held or pending or human or fresh):
        parts.append("이상 없음 · 갱신할 것 없음")
    parts.append(f"_상세 로그: data/logs/auto_update_{today:%Y%m%d}.log_")
    text = "\n".join(parts)
    say("\n" + text)
    _write_log(today)
    if not a.no_slack:
        slack(text)
    return 1 if held else 0


def _write_log(today: date) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    f = LOG_DIR / f"auto_update_{today:%Y%m%d}.log"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(f"\n##### {datetime.now():%H:%M:%S}\n" + "\n".join(LOG) + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        LOG.append(traceback.format_exc())
        _write_log(date.today())
        if "--no-slack" not in sys.argv:
            slack(f"🗺️ *지역분석 자동갱신 오류* — {type(e).__name__}: {e}\n_data/logs/auto_update_{date.today():%Y%m%d}.log_")
        raise
