"""check_childcare_key.py — 어린이집 정보공개포털 키 점검 — **읽기 전용**

    python scripts/check_childcare_key.py                (기본 26350 해운대구)
    python scripts/check_childcare_key.py --arcode 11680

무엇을 보는가
  ① 키가 살아 있는가 (HTTP 200 · <item> 파싱)
  ② **개발키인가 운영키인가** ← 이게 핵심이다

[중요] 개발키는 값이 전부 `01, 02, 03 …` 순번 더미로 돌아온다. HTTP 200 이고 필드도 73개
   그대로라서 **성공처럼 보인다.** 포털 공지와 실측이 일치한다(2026-08-11).
   이걸 모르고 파이프라인을 짜면 "어린이집 이름이 04 로 들어온다"고 헤매게 된다.
   실데이터는 **운영계정 승인 후 발급되는 운영키**에서만 나온다.

원천: https://info.childcare.go.kr  (보육정보공개 API)
  GET http://api.childcare.go.kr/mediate/rest/cpmsapi030/cpmsapi030/request
      ?key=<키>&arcode=<시군구코드 5자리>
"""
import argparse
import pathlib
import re
import sys
import urllib.error
import urllib.request

# 이 폴더의 다른 스크립트와 같은 규약 (verify_c8.py · collect_kapt.py · check_datago_key.py)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "http://api.childcare.go.kr/mediate/rest/cpmsapi030/cpmsapi030/request"

# 만0~5세 연령별 현원. 유치원(만3~5세)이 못 잡는 구간을 이 필드가 채운다.
AGE_FIELDS = [f"CHILD_CNT_{n:02d}" for n in range(6)]


def read_key():
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("CHILDCARE_KEY=") and s.split("=", 1)[1].strip():
            return s.split("=", 1)[1].strip()
    return None


def fetch(key, arcode):
    url = f"{API}?key={key}&arcode={arcode}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


def parse_items(body):
    out = []
    for chunk in re.findall(r"<item>(.*?)</item>", body, re.S):
        out.append(dict(re.findall(r"<(\w+)>([^<]*)</\1>", chunk)))
    return out


def looks_like_dummy(item):
    """값이 01,02,03… 순번이면 개발키다. 앞쪽 필드 몇 개만 봐도 갈린다."""
    probe = [item.get("sidoname"), item.get("sigunname"), item.get("stcode")]
    return probe == ["01", "02", "03"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="어린이집 정보공개포털 키 점검")
    ap.add_argument("--arcode", default="26350", help="시군구코드 5자리 (기본 26350 해운대구)")
    a = ap.parse_args(argv)

    key = read_key()
    if not key:
        print("[!!] .env 의 CHILDCARE_KEY 가 비어 있다.")
        print("     https://info.childcare.go.kr → 보육정보공개 API → OPEN API 이용 신청")
        return 1
    print(f"[key] .env 에서 읽음 (길이 {len(key)})")

    try:
        status, body = fetch(key, a.arcode)
    except urllib.error.HTTPError as e:
        print(f"[!!] HTTP {e.code} — 키 미승인이거나 서비스 오류")
        print("     " + " ".join(e.read(300).decode("utf-8", "replace").split())[:200])
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[!!] 호출 실패 {type(e).__name__}: {e}")
        return 1

    items = parse_items(body)
    print(f"[호출] arcode={a.arcode} · HTTP {status} · item {len(items)}건")
    if not items:
        print("[!!] item 이 0건이다 — arcode 가 틀렸거나 승인 범위 밖이다.")
        return 1

    first = items[0]
    if looks_like_dummy(first):
        print()
        print("  [개발키] **더미다 — 값이 전부 더미(01, 02, 03 …)**")
        print(f"     예: crname={first.get('crname')} · craddr={first.get('craddr')}"
              f" · crchcnt={first.get('crchcnt')}")
        print(f"     필드 구조는 정상 {len(first)}개 — 연동 코드는 이 상태로도 짤 수 있다.")
        print()
        print("  → 실데이터를 받으려면 **운영계정 신청·승인**이 필요하다.")
        print("     info.childcare.go.kr → 보육정보공개 API → OPEN API 이용 신청 (신청구분: 운영)")
        return 2

    print()
    print("  [운영키] **실데이터다 — 실데이터가 나온다.**")
    print(f"     {first.get('crname')} · {first.get('craddr')}")
    print(f"     정원 {first.get('crcapat')} · 현원 {first.get('crchcnt')}"
          f" · 좌표 ({first.get('la')}, {first.get('lo')})")
    ages = {f: first.get(f) for f in AGE_FIELDS}
    print(f"     연령별 현원 만0~5세: {ages}")
    missing = [f for f in AGE_FIELDS if first.get(f) in (None, "")]
    if missing:
        print(f"     ※ 비어 있는 연령 필드: {missing} (해당 반이 없으면 실제로 빈다)")
    print(f"     수집기를 붙일 수 있다. 전국은 시군구 {a.arcode} 자리를 250여 개 순회.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
