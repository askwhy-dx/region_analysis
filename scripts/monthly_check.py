# -*- coding: utf-8 -*-
"""monthly_check.py — 매월 첫 평일 전체 점검 (E-3) — **읽기 전용**

무엇을 하나
-----------
데이터를 **고치지 않는다.** "지금 갱신할 때가 됐는가"를 판정해서 보고만 한다.
갱신은 이 리포트를 보고 사람이 배치를 고르는 것으로 한다(2026-08-11 관리자 결정).

  ① 원천 신선도  — 인구·아파트·세대수·경쟁사·학교·**어린이집**이 얼마나 낡았는지
  ② 경계 새 버전 — admdongkor 에 `DEFAULT_VER` 보다 최신이 올라왔는지        [E-3 ①]
  ③ 학교 기준연도 — 연 1회 공시라 **해가 바뀌어도 아무도 모른다**            [E-3 ③]
  ③-b 어린이집 포털 코드표 — `arcode` 가 개편되면 그 시군구가 **0개로 보인다**  [2026-08-13]
  ④ 데이터 검증  — verify_c8.py
  ⑤ 화면 검증    — verify_dashboard.js
  ⑥ 발행 동기    — 공개본이 로컬과 같은지 (deploy_pages.py --dry-run)

⚠️ **이 점검의 존재 이유는 "조용히 낡는 것"이다.**
   낡은 데이터는 화면이 정상으로 보이고 배지도 "실데이터"로 뜬다. 원천이 갱신됐다는
   사실을 알려 주는 장치가 어디에도 없어서, 감시를 안 걸면 작년 학생수로 상담한다.

사용
----
  python scripts/monthly_check.py              # 전체 점검
  python scripts/monthly_check.py --quick      # 화면 검증·발행 동기 생략 (빠름)
  python scripts/monthly_check.py --no-net     # 네트워크 점검 전부 생략

종료코드 — 0 정상 / 1 조치 필요(FAIL) / 0 이지만 TODO 가 있으면 리포트에 뜬다
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ⚠️ **콘솔 인코딩을 UTF-8 로 강제하지 않는다.**
# 여기서 `reconfigure(encoding="utf-8")` 를 하면 cp949 콘솔(한국어 Windows 기본)에
# UTF-8 바이트가 그대로 쏟아져 **화면 전체가 깨진다.** 실제로 그렇게 만들어 놓고
# 한 번 깨뜨렸다. 콘솔이 정한 인코딩을 그대로 쓰고, 표현 못 하는 문자만 대체한다.
# (파이프·파일로 리다이렉트될 때는 파이썬이 UTF-8 을 고르므로 로그는 온전하다.)
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"
PY = sys.executable
LOG_DIR = ROOT / "data" / "logs"

# 원천별 "이 정도 지나면 갱신할 때" 임계 (개월). 원천의 공표 주기 + 지연을 감안한 값이다.
#   인구   : 행안부 주민등록, 월 1회 공표(익월 중순) → 2개월까지는 정상
#   아파트 : RTMS 익월 공개 → 최신 거래월이 2개월 이상 뒤처지면 갱신
#   세대수 : K-apt, 월 1회 갱신 권장
#   경쟁사 : 크롤, 월 1회 갱신 (COMPETITOR.md)
#   어린이집: 포털이 상시 갱신 · 원본을 월(base_ym) 폴더로 받으므로 월 1회
FRESH_LIMIT = {"population": 2, "apartment": 2, "kapt": 1, "competitor": 1, "childcare": 1}

RESULT: list[tuple[str, str, str]] = []


def ok(name: str, detail: str = "") -> None:
    RESULT.append(("OK", name, detail))


def todo(name: str, detail: str) -> None:
    """갱신할 때가 됐다 — 실패는 아니다."""
    RESULT.append(("TODO", name, detail))


def fail(name: str, detail: str) -> None:
    RESULT.append(("FAIL", name, detail))


def meta_of(fname: str) -> dict:
    p = P / fname
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("meta", {})
    except Exception:
        return {}


def ym_int(s: str) -> int:
    """'202606' → 24312 (개월 절대값). 뺄셈으로 개월차를 낸다."""
    return int(s[:4]) * 12 + int(s[4:6])


def months_since(ym: str, today: date) -> int:
    return (today.year * 12 + today.month) - ym_int(ym)


# ---------------------------------------------------------------- ① 신선도

# 이 숫자를 넘으면 심판을 돌릴 때다. 2026-08-12 기준 56개가 정상 범위였다 —
# 여기서 크게 늘면 경계 원천이 현실보다 더 뒤처졌다는 뜻이다.
STALE_LIMIT = 60


def meta_and_data(fname: str) -> dict:
    p = P / fname
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("data") or {}
    except Exception:  # noqa: BLE001
        return {}


def meta_only(fname: str) -> dict:
    p = P / fname
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("meta") or {}
    except Exception:  # noqa: BLE001
        return {}


def check_freshness(today: date) -> None:
    # 인구 — base_ym
    m = meta_of("population.json")
    if not m.get("base_ym"):
        fail("인구 기준월", "population.json 에 meta.base_ym 이 없다")
    else:
        gap = months_since(m["base_ym"], today)
        d = f"기준월 {m['base_ym']} · {gap}개월 전"
        (ok if gap <= FRESH_LIMIT["population"] else todo)(
            "인구 기준월", d + ("" if gap <= FRESH_LIMIT["population"] else "  -> collect_population.py"))

    # 아파트 실거래 — period 의 끝 월
    m = meta_of("apartment.json")
    period = m.get("period", "")
    mo = re.search(r"~(\d{6})", period)
    if not mo:
        fail("아파트 실거래", f"period 를 읽지 못했다: {period!r}")
    else:
        gap = months_since(mo.group(1), today)
        d = f"최신 거래월 {mo.group(1)} · {gap}개월 전"
        (ok if gap <= FRESH_LIMIT["apartment"] else todo)(
            "아파트 실거래", d + ("" if gap <= FRESH_LIMIT["apartment"] else "  -> collect_rtms.py (!! --verify-only 먼저)"))

    # 어린이집 — 부분수집이면 신선도보다 이게 먼저다. 빈 시군구는 화면에서 **0개**로 보인다.
    m = meta_of("childcare.json")
    if m.get("partial"):
        un = ", ".join((m.get("unresolved_sgg") or {}).values()) or "?"
        fail("어린이집 부분수집", f"미수집 시군구: {un}  -> fetch_childcare_arcode.py 로 코드 확인")

    # K-apt 세대수 · 경쟁사 · 어린이집 — base_ym
    for fname, key, label, batch in (
        ("kapt.json", "kapt", "아파트 세대수", "update_kapt.bat"),
        ("competitor_adm.json", "competitor", "자사·경쟁사 위치", "update_competitor.bat"),
        # ⚠️ update_childcare.bat 은 **잔여 수집 전용**이다. 월 갱신은 전량 재수집이라 다르다.
        ("childcare.json", "childcare", "어린이집 원아",
         "python scripts\\collect_childcare.py (전량) -> build_metrics.py -> publish.bat"),
    ):
        m = meta_of(fname)
        if not m.get("base_ym"):
            fail(label, f"{fname} 에 meta.base_ym 이 없다")
            continue
        gap = months_since(m["base_ym"], today)
        d = f"기준월 {m['base_ym']} · {gap}개월 전"
        (ok if gap <= FRESH_LIMIT[key] else todo)(
            label, d + ("" if gap <= FRESH_LIMIT[key] else f"  -> {batch}"))


# ------------------------------------------------ ②-b 낡은 경계 의심 동 [2026-08-12]

def check_stale_boundary() -> None:
    """경계 폴리곤이 낡으면 값이 옆 동에 쌓인다. 그 증상이 **아파트 세대수 > 주민등록
    세대수**다(물리적으로 불가능하니 배정이 틀린 것이다).

    ⚠️ 이 지표가 **유일한 자동 탐지 수단**이다. 학원·경쟁사에는 대조할 물리량이 없어
       경계가 낡아도 아무 신호가 안 뜬다 — 광주 하남동 건은 사람이 현장에서 발견했다.
       그래서 여기 숫자가 늘면 **다른 축도 같이 틀어졌다고 봐야 한다.**

    심판(`arbitrate_adm.py --scope suspect`)은 이 동들에 소재한 건을 JUSO 에 다시 물어
    낡은 폴리곤을 이긴다. 매달 돌릴 필요는 없고, **숫자가 늘었을 때** 돌리는 게 맞다.
    """
    met = meta_and_data("metrics.json")
    if not met:
        fail("낡은 경계 의심", "metrics.json 을 읽지 못했다")
        return
    sus = [(c, v.get("name"), v.get("apt_ratio")) for c, v in met.items()
           if v.get("apt_ratio_suspect")]
    # ⚠️ meta 가 아니라 **항목에서 직접 센다.** 지오코더가 캐시를 다시 저장할 때 문서
    #    레벨 meta 를 갈아 끼우는 일이 있어(2026-08-12 수정) meta 만 믿으면 이력이 사라진다.
    ent = meta_and_data("geocode_cache.json")
    gc = {"reverted": sum(1 for v in ent.values() if v and v.get("arbiter") == "pip_outlier"),
          "apt_ratio_restored": sum(1 for v in ent.values()
                                    if v and v.get("outlier_override") == "apt_ratio_guard")}
    top = ", ".join(f"{n} {r}" for _, n, r in sorted(sus, key=lambda x: -(x[2] or 0))[:3])
    d = f"거주비율 100% 초과 {len(sus)}개 동" + (f" · 상위 {top}" if top else "")
    if len(sus) > STALE_LIMIT:
        todo("낡은 경계 의심", d + f"  -> arbitrate_adm.py --scope suspect (기준 {STALE_LIMIT}개)")
    else:
        ok("낡은 경계 의심", d)
    if gc.get("reverted"):
        ok("심판 외톨이 가드", f"되돌림 {gc.get('reverted')}건 · 거주비율 복원 "
                            f"{gc.get('apt_ratio_restored', 0)}건 (2026-08-12 도입)")


# ------------------------------------------------------- ③ 학교 기준연도 [E-3 ③]

def check_school_year(today: date) -> None:
    """학교알리미는 **연 1회 공시**라 자동 감지가 원리적으로 불가능하다.
    그래서 '해가 바뀌었는데 기준연도가 그대로면 경고'가 유일한 장치다."""
    m = meta_of("school.json")
    by = m.get("base_year")
    if not by:
        fail("학교 기준연도", "school.json 에 meta.base_year 가 없다")
        return
    if by >= today.year:
        ok("학교 기준연도", f"{by}년 (올해 공시분)")
    elif by == today.year - 1 and today.month <= 4:
        # 신학년도 공시는 보통 봄에 올라온다 — 1~4월은 작년치가 최신인 게 정상이다.
        ok("학교 기준연도", f"{by}년 · 신학년도 공시 대기 (통상 4~5월 공개)")
    else:
        todo("학교 기준연도",
             f"{by}년 — {today.year - by}년 낡았다  -> 학교알리미에서 CSV 재수령 후 load_school.py --src <폴더>")


# ------------------------------------------------------- ② 경계 새 버전 [E-3 ①]

def check_childcare_arcode(no_net: bool) -> None:
    """어린이집 포털의 `arcode` 개편 감지 [2026-08-13 신설]

    포털은 **행안부 표준 시군구코드를 안 쓰는 지역이 있다.** 표준코드로 물으면
    `HTTP 200 + INFO-200` 이 와서 **"그 지역에 어린이집이 0개"와 응답이 똑같다.**
    구가 신설되거나 시로 승격하면 그날부터 그 시군구가 조용히 빈다 — 알려 주는 장치가 없다.

    포털 화면이 쓰는 코드 목록을 받아 **우리 code_map 을 한 줄씩 대조**한다.
    ⚠️ 이 조회는 **API 호출 한도를 안 쓴다**(포털 JSP). 코드를 스캔으로 찾지 말 것 —
       2026-08-12 에 2,300개를 스캔했다가 한도를 태워 되던 코드까지 0건이 됐다.
    """
    if no_net:
        ok("어린이집 코드표", "(네트워크 점검 생략)")
        return
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from collect_childcare import ARCODE_OVERRIDE  # noqa: PLC0415
        from fetch_childcare_arcode import sgg_codes, sido_codes  # noqa: PLC0415

        portal: dict[str, str] = {}
        for code, _ in sido_codes():
            portal.update(dict(sgg_codes(code)))
    except Exception as e:  # noqa: BLE001
        fail("어린이집 코드표", f"포털 코드표를 못 받았다: {type(e).__name__} — 화면 개편 의심")
        return

    if len(portal) < 200:
        fail("어린이집 코드표", f"표가 너무 작다({len(portal)}개) — 파싱이 깨졌을 가능성")
        return

    codes = list((meta_and_data("metrics_sgg.json") or {}).keys())   # 시군구 전수(256)
    missing = []
    for c in codes:
        if c in portal or c in ARCODE_OVERRIDE:
            continue
        if c[:4] + "0" in portal:   # 구 단위는 표에 없다 — 소속 시가 있으면 커버된 것
            continue
        missing.append(c)
    if missing:
        fail("어린이집 코드표",
             f"포털에 코드가 없는 시군구 {len(missing)}개: {', '.join(missing[:8])}"
             "  -> fetch_childcare_arcode.py 로 확인 후 ARCODE_OVERRIDE 등재")
        return
    # 오버라이드가 가리키는 코드가 표에서 사라졌으면 그 지역이 다음 수집부터 빈다
    gone = [f"{s}->{t}" for s, t in ARCODE_OVERRIDE.items()
            if t not in portal and t[:4] + "0" not in portal]
    if gone:
        fail("어린이집 코드표", f"오버라이드 대상이 표에서 사라졌다: {', '.join(gone)}")
        return
    ok("어린이집 코드표", f"포털 시군구 {len(portal)}개 · 대조 {len(codes)}개 · 불일치 0")


def check_boundary_ver(no_net: bool) -> None:
    """`geo_build.DEFAULT_VER` 은 하드코딩이고, admdongkor 는 약 분기별로 갱신된다.
    새 버전이 올라와도 알려 주는 장치가 없어서 여기서 본다."""
    cur = None
    try:
        src = (ROOT / "scripts" / "geo_build.py").read_text(encoding="utf-8")
        mo = re.search(r"DEFAULT_VER\s*=\s*'(\d{8})'", src)
        cur = mo.group(1) if mo else None
    except Exception:
        pass
    if not cur:
        fail("경계 버전", "geo_build.py 에서 DEFAULT_VER 를 읽지 못했다")
        return
    if no_net:
        ok("경계 버전", f"현재 ver{cur} (네트워크 점검 생략)")
        return

    try:
        import requests
        r = requests.get(
            "https://api.github.com/repos/vuski/admdongkor/contents/",
            headers={"Accept": "application/vnd.github+json"}, timeout=20)
        r.raise_for_status()
        vers = sorted(re.sub(r"^ver", "", d["name"]) for d in r.json()
                      if d.get("type") == "dir" and re.fullmatch(r"ver\d{8}", d.get("name", "")))
    except Exception as e:
        # 네트워크·한도 문제로 못 봤다는 것과 "최신이다"를 구분한다.
        fail("경계 버전", f"목록 조회 실패 ({type(e).__name__}) — 다음 회차에 다시 본다")
        return

    if not vers:
        fail("경계 버전", "admdongkor 에서 ver 디렉터리를 찾지 못했다 (리포 구조가 바뀌었나)")
        return
    latest = vers[-1]
    if latest <= cur:
        ok("경계 버전", f"ver{cur} 가 최신 (원격 {len(vers)}종)")
    else:
        newer = [v for v in vers if v > cur]
        todo("경계 버전",
             f"ver{cur} → **ver{latest}** 신규 {len(newer)}종({', '.join(newer)})  "
             f"-> geo_build.py DEFAULT_VER 교체 후 전 파이프라인 재실행 "
             f"[주의] 경계가 바뀌면 동별 수치를 옛 산출물과 비교 금지")


# ---------------------------------------------------------------- ④⑤⑥ 실행

def run(label: str, cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, "(30분 초과)"
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out


def tail(text: str, n: int = 3) -> str:
    """검증 스크립트의 한 줄 요약. 두 검증 다 '판정 — PASS n · FAIL n' 을 찍으므로
    그 줄을 먼저 찾고, 없을 때만 끝 몇 줄을 쓴다(경고 목록이 끝에 붙어 있어서)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 실패한 시험 이름도 붙인다 — 「FAIL 1」만으로는 슬랙에서 무엇이 깨졌는지 모른다 (2026-10-12)
    failed = list(dict.fromkeys(l[4:].split(" — ")[0].strip() for l in lines if l.startswith("[!!]")))
    extra = (" · 실패: " + ", ".join(failed[:5])) if failed else ""
    for l in reversed(lines):
        if "판정" in l and ("PASS" in l or "FAIL" in l):
            return (l + extra)[:400]
    return (" / ".join(lines[-n:]) + extra)[:400]


# ------------------------------------------- ①-b 원천 최신월 → 슬랙 안내 [2026-10-12]
# ① 은 "우리 파일이 몇 달 됐나"만 본다. 여기서는 "원천에 우리보다 새 달이 나왔나"를 본다.
#   인구   : jumin 에 시도 합계 CSV 를 실제로 받아 본다 — 미공표 달은 전부 0 으로 내려온다(실측).
#   실거래 : 신고기한 30일이라 직전 완료월이 최신 (collect_rtms.default_period 와 같은 기준)
#   세대수·어린이집 : 원천이 상시/월 갱신이라 이번 달 수집분이 최신
SLACK_CHANNEL = "D0BA6SULQUU"  # 유삐 DM (사용자 지정 2026-10-12)
NEWER: list[str] = []


def _prev_ym(today: date, n: int = 1) -> str:
    t = today.year * 12 + today.month - 1 - n
    return f"{t // 12:04d}{t % 12 + 1:02d}"


def _jumin_has(ym: str) -> bool:
    sys.path.insert(0, str(ROOT / "scripts"))
    import requests
    import collect_population as cp
    form = {"sltOrgType": "1", "sltOrgLvl1": "A", "sltOrgLvl2": "", "sum": "sum", "sltUndefType": "",
            "searchYearStart": ym[:4], "searchMonthStart": ym[4:],
            "searchYearEnd": ym[:4], "searchMonthEnd": ym[4:],
            "sltOrderType": "1", "sltOrderValue": "ASC", "sltArgTypes": "10",
            "sltArgTypeA": "0", "sltArgTypeB": "100", "category": "month"}
    b = cp._jumin_post(requests.Session(), "ageStatMonth.do", "downloadCsvAge.do",
                       {"searchYearMonth": "month", "xlsStats": "1"}, form, timeout=60)
    rows = [r for r in cp.decode_csv(b).splitlines()[1:] if r.strip()]
    return any(re.search(r'"[1-9][\d,]*"', r) for r in rows)


def check_source_latest(today: date, no_net: bool) -> None:
    cur = f"{today:%Y%m}"
    pop = None
    if not no_net:
        try:
            pop = next((ym for ym in (_prev_ym(today, n) for n in (1, 2, 3)) if _jumin_has(ym)), None)
        except Exception as e:  # noqa: BLE001
            fail("원천 최신월(인구)", f"jumin 확인 실패: {e}")
    ap = re.search(r"~(\d{6})", meta_of("apartment.json").get("period", ""))
    for label, ours, latest, how, batch in (
        ("인구", meta_of("population.json").get("base_ym"), pop, "행안부 확인",
         "collect_population.py"),
        ("아파트 실거래", ap and ap.group(1), _prev_ym(today), "신고기한 기준",
         "collect_rtms.py (--verify-only 먼저)"),
        ("아파트 세대수", meta_of("kapt.json").get("base_ym"), cur, "월 갱신", "update_kapt.bat"),
        ("어린이집 원아", meta_of("childcare.json").get("base_ym"), cur, "상시 갱신",
         "collect_childcare.py (전량)"),
    ):
        if ours and latest and latest > ours:
            NEWER.append(f"• {label}: 보유 {ours} → 원천 *{latest}* ({how}) → `{batch}`")


def post_slack(today: date) -> None:
    """새 데이터 안내 + FAIL 항목을 한 메시지로. 둘 다 없으면 보내지 않는다."""
    fails = [f"• {n}: {d}" for st, n, d in RESULT if st == "FAIL"]
    if not NEWER and not fails:
        return
    parts = [f"🗺️ *지역분석 월간점검 {today:%Y-%m-%d}*"]
    if fails:
        parts += ["🔴 *실패 — 먼저 원인 확인 (검증이 깨진 채 발행 금지)*"] + fails
    if NEWER:
        parts += ["🆕 *원천에 새 데이터*"] + NEWER + ["_갱신 후 build_metrics → publish 필요_"]
    parts.append(f"_상세 로그: data/logs/monthly_check_{today:%Y%m%d}.log_")
    text = "\n".join(parts)
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
        print("[slack] SLACK_BOT_TOKEN 없음 — 안내 건너뜀 (test\\.env)")
        return
    import urllib.request
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": SLACK_CHANNEL, "text": text}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        print(f"[slack] ok={res.get('ok')}" + ("" if res.get("ok") else f" err={res.get('error')}"))
    except Exception as e:  # noqa: BLE001
        print(f"[slack] 전송 실패: {e}")


def check_verify(quick: bool) -> str:
    logs = []

    rc, out = run("데이터", [PY, "scripts/verify_c8.py"], ROOT)
    logs.append("=== verify_c8.py ===\n" + out)
    (ok if rc == 0 else fail)("데이터 검증", tail(out))

    if quick:
        ok("화면 검증", "(--quick 으로 생략)")
        ok("발행 동기", "(--quick 으로 생략)")
        return "\n".join(logs)

    rc, out = run("화면", ["node", "scripts/verify_dashboard.js"], ROOT)
    logs.append("=== verify_dashboard.js ===\n" + out)
    (ok if rc == 0 else fail)("화면 검증", tail(out))

    rc, out = run("발행", [PY, "scripts/deploy_pages.py", "--dry-run"], ROOT)
    logs.append("=== deploy_pages.py --dry-run ===\n" + out)
    if rc != 0:
        fail("발행 동기", tail(out))
    elif "바뀐 게 없다" in out:
        ok("발행 동기", "공개본 = 로컬 (변경 0개)")
    else:
        mo = re.search(r"변경\s*(\d+)개", out)
        todo("발행 동기",
             f"공개본이 로컬보다 낡았다 (변경 {mo.group(1) if mo else '?'}개)  -> publish.bat")
    return "\n".join(logs)


# ---------------------------------------------------------------- 리포트

MARKER = LOG_DIR / "monthly_check_last.txt"


def is_holiday(d: date) -> bool | None:
    """공공데이터 특일정보로 공휴일 판정. **모르면 None** 을 준다.
    조회 실패를 '평일'로 단정하면 신정에 점검이 돌고, '휴일'로 단정하면
    그 달 점검이 통째로 빠진다 — 그래서 셋을 구분한다."""
    key = None
    for env in (ROOT.parent / ".env", ROOT / ".env"):
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DATA_GO_KR_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key or key.startswith("<"):
        return None
    try:
        import requests
        r = requests.get(
            "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo",
            params={"serviceKey": key, "solYear": d.year, "solMonth": f"{d.month:02d}",
                    "_type": "json", "numOfRows": 50}, timeout=15)
        items = r.json()["response"]["body"].get("items") or {}
        item = items.get("item") or []
        if isinstance(item, dict):
            item = [item]
        days = {str(i.get("locdate")) for i in item if i.get("isHoliday") == "Y"}
        return f"{d:%Y%m%d}" in days
    except Exception:
        return None


def gate(today: date) -> int:
    """오늘 점검을 돌려야 하는가. 돌려야 하면 0, 아니면 1.

    스케줄러는 매월 1~5일 매일 부르고, 실제 실행 여부는 여기서 정한다.
    `schtasks` 에 '첫 평일'이라는 조건이 없어서 이렇게 나눴다."""
    if today.day > 5:
        # 스케줄러는 1~5일만 부른다. 그 밖에 불렸다면 사람이 수동으로 돌린 것이니
        # 게이트를 통과시키지 않는다(수동 점검은 --gate 없이 그냥 돌리면 된다).
        print(f"[gate] {today:%Y-%m-%d} 은 월초(1~5일)가 아니다 — 건너뛴다")
        return 1
    if today.weekday() >= 5:
        print(f"[gate] {today:%Y-%m-%d} 주말 — 건너뛴다")
        return 1
    if MARKER.is_file() and MARKER.read_text(encoding="utf-8").strip() == f"{today:%Y%m}":
        print(f"[gate] {today:%Y%m} 점검은 이미 끝났다 — 건너뛴다")
        return 1
    h = is_holiday(today)
    if h is True:
        print(f"[gate] {today:%Y-%m-%d} 공휴일 — 건너뛴다 (다음 평일에 실행)")
        return 1
    if h is None:
        print("[gate] 공휴일 조회 불가 — 평일로 보고 진행한다")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(f"{today:%Y%m}", encoding="utf-8")
    print(f"[gate] {today:%Y-%m-%d} 이번 달 첫 평일 — 점검을 시작한다")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="화면 검증·발행 동기 생략")
    ap.add_argument("--no-net", action="store_true", help="네트워크 점검 전부 생략")
    ap.add_argument("--gate", action="store_true",
                    help="오늘이 이번 달 첫 평일인지만 판정 (스케줄러용, 점검은 안 함)")
    a = ap.parse_args()

    today = date.today()
    if a.gate:
        return gate(today)
    print("=" * 66)
    print(f"  지역분석 월간 점검 — {today:%Y-%m-%d}")
    print("=" * 66)

    check_freshness(today)
    check_source_latest(today, a.no_net)
    check_school_year(today)
    check_childcare_arcode(a.no_net)
    check_boundary_ver(a.no_net)
    check_stale_boundary()
    logs = "" if a.no_net else check_verify(a.quick)
    if a.no_net:
        ok("데이터 검증", "(--no-net 으로 생략)")

    print()
    mark = {"OK": "  OK ", "TODO": " 갱신", "FAIL": " 실패"}
    for st, name, detail in RESULT:
        print(f"[{mark[st]}] {name:<16} {detail}")

    fails = [r for r in RESULT if r[0] == "FAIL"]
    todos = [r for r in RESULT if r[0] == "TODO"]
    print()
    print("-" * 66)
    print(f"판정 — 정상 {sum(1 for r in RESULT if r[0]=='OK')} · 갱신필요 {len(todos)} · 실패 {len(fails)}")

    if fails:
        print()
        print("[실패] 먼저 원인을 확인할 것. 검증이 깨진 채로 발행하면 안 된다.")
        for _, n, d in fails:
            print(f"   · {n}: {d}")
    if todos:
        print()
        print("[갱신] 아래 배치를 돌리면 검증·발행까지 이어진다.")
        for _, n, d in todos:
            print(f"   · {n}: {d}")
    if not fails and not todos:
        print()
        print("[정상] 전부 최신이다. 이번 달은 할 일이 없다.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"monthly_check_{today:%Y%m%d}.log"
    body = [f"지역분석 월간 점검 {today:%Y-%m-%d}", ""]
    body += [f"[{st}] {n}\t{d}" for st, n, d in RESULT]
    body += ["", logs]
    body += ["", "[원천 최신월]"] + (NEWER or ["새 데이터 없음"])
    log.write_text("\n".join(body), encoding="utf-8")
    print()
    print(f"로그 — {log}")
    if NEWER:
        print("\n[원천에 새 데이터]\n" + "\n".join(NEWER))
    if not a.no_net:
        post_slack(today)

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
