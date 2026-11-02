# -*- coding: utf-8 -*-
"""check_datago_key.py — 공공데이터포털 키(`DATA_GO_KR_KEY`)가 실제로 먹는지 확인한다.

  python scripts/check_datago_key.py

`.env` 의 `DATA_GO_KR_KEY` 를 읽어 **공동주택 단지 목록 → 기본정보** 두 API 를
각각 한 번씩 호출해 본다. 세대수(`kaptdaCnt`)까지 실제로 내려오는지 확인하면
그 키로 K-apt 수집기를 붙일 수 있다는 뜻이다.

⚠️ 포털은 키를 **인코딩/디코딩 2종**으로 준다. 겉보기가 비슷해 헷갈리는데
   (`%2B`·`%3D` 가 들어 있으면 인코딩 키다) 잘못 쓰면
   `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` 가 뜬다.
   이 스크립트는 **둘 다 시도**하고 어느 쪽이 맞는지 알려준다 —
   `.env` 에는 그냥 포털에서 복사한 값을 붙여 넣으면 된다.

⚠️ 활용신청 직후에는 몇 분~1시간 정도 반영이 늦을 수 있다.
   `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` 만 나오면 조금 뒤 다시 돌려볼 것.
"""

from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]

# ⚠️ 목록 API 는 오퍼레이션마다 **입력 코드가 다르다.**
#    getSigunguAptList3  → sigunguCode(5자리)   ← 우리가 쓰는 것
#    getLegaldongAptList3 → bjdCode(10자리)
#    getRoadnameAptList3  → 도로명코드          ← 시군구코드를 넣으면 인증은 통과하고
#                                               **totalCount 0** 으로 조용히 빈다
#    getTotalAptList3     → 전국 전체(22,256단지, 페이지네이션)
LIST_API = "https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"
BASIS_API = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"

# 확인용 표본 — 서울 강남구 (233단지)
SAMPLE_SGG = "11680"


def load_key() -> str:
    f = ROOT / ".env"
    if not f.exists():
        raise SystemExit("[FATAL] .env 가 없다")
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATA_GO_KR_KEY") and "=" in line:
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            if v:
                return v
    raise SystemExit(
        "[FATAL] .env 의 DATA_GO_KR_KEY 가 비어 있다\n"
        "        포털 마이페이지 > 인증키 발급현황 에서 복사해 붙여 넣을 것")


def sanity(key: str) -> str:
    """붙여넣기 사고를 먼저 잡는다 — 여기서 걸리는 게 API 에러보다 훨씬 친절하다.

    ⚠️ 포털은 **같은 키를 인코딩/디코딩 두 형태로** 보여준다. 서로 다른 키가
       아니므로 둘을 함께 넣으면 안 된다(공백·줄바꿈으로 이어 붙이는 실수가 흔하다).
       API 2개를 신청해도 인증키는 **계정당 하나**다.
    """
    if any(c in key for c in " \t"):
        raise SystemExit(
            "[FATAL] 키에 공백이 들어 있다 — 두 개를 붙여 넣은 것 같다.\n"
            "        포털의 인코딩/디코딩 키는 **같은 키의 다른 표기**다. 하나만 넣을 것\n"
            "        (권장: `%` 가 없는 Decoding 키). 활용신청을 2건 했어도 키는 하나다.")
    if len(key) < 40:
        raise SystemExit(f"[FATAL] 키가 너무 짧다({len(key)}자) — 일부만 복사된 것 같다")
    if key.startswith(("'", '"')) or key.endswith(("'", '"')):
        raise SystemExit("[FATAL] 따옴표가 붙어 있다 — 따옴표 없이 값만 넣을 것")
    return key


def call(url: str, key: str, params: dict) -> tuple[int, str]:
    """serviceKey 는 이미 인코딩된 값일 수 있어 직접 붙인다(이중 인코딩 방지)."""
    q = urllib.parse.urlencode(params)
    full = f"{url}?serviceKey={key}&{q}"
    req = urllib.request.Request(full, headers={"User-Agent": "jiyeok-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def diagnose(body: str) -> str | None:
    for code, msg in (
        ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "키가 아직 등록/반영 안 됨 (신청 직후면 잠시 뒤 재시도)"),
        ("SERVICE ACCESS DENIED", "이 API 에 활용신청을 안 했다"),
        ("LIMITED_NUMBER_OF_SERVICE_REQUESTS", "일일 호출 한도 초과 (개발계정 10,000건)"),
        ("HTTP ROUTING ERROR", "엔드포인트 주소 오류"),
        ("UNAUTHORIZED", "인증 실패"),
    ):
        if code in body.upper():
            return f"{code} — {msg}"
    return None


def main() -> int:
    raw = sanity(load_key())
    print(f"[key] .env 에서 읽음 (길이 {len(raw)}, "
          f"{'인코딩키로 보임' if '%' in raw else '디코딩키로 보임'})")

    # 인코딩/디코딩 둘 다 시도한다 — 어느 쪽이 맞는지는 돌려봐야 안다
    cands = [("원본 그대로", raw)]
    if "%" in raw:
        cands.append(("URL 디코딩본", urllib.parse.unquote(raw)))
    else:
        cands.append(("URL 인코딩본", urllib.parse.quote(raw, safe="")))

    ok_key = None
    for label, k in cands:
        st, body = call(LIST_API, k, {"sigunguCode": SAMPLE_SGG, "pageNo": 1,
                                      "numOfRows": 3, "_type": "json"})
        why = diagnose(body)
        head = body[:120].replace("\n", " ")
        print(f"\n[단지목록] {label}: HTTP {st}")
        if why:
            print(f"   ✗ {why}")
            continue
        if "kaptCode" in body or "kaptName" in body:
            print(f"   ✅ 응답 정상 — {head}")
            ok_key = k
            break
        print(f"   ? 판단 불가 — {head}")

    if not ok_key:
        print("\n❌ 단지 목록 API 가 응답하지 않는다. 위 사유를 확인할 것.")
        return 1

    # 단지 몇 개를 뽑아 기본정보(세대수)까지 확인한다.
    # ⚠️ 한 건만 보면 안 된다 — **세대수가 0 으로 비어 있는 단지가 실제로 있다**
    #    (소규모·오피스텔형). 한 건 찍고 "성공"이라고 하면 나중에 전국 수집에서
    #    0 이 쏟아지는 걸 여기서 못 잡는다.
    import re as _re
    codes = _re.findall(r'"kaptCode"\s*:\s*"([^"]+)"',
                        call(LIST_API, ok_key,
                             {"sigunguCode": SAMPLE_SGG, "pageNo": 1,
                              "numOfRows": 5, "_type": "json"})[1])
    if not codes:
        print("\n⚠️ 목록은 되는데 kaptCode 를 못 뽑았다 — 응답 형식 확인 필요")
        return 1

    print(f"\n[기본정보] 표본 {len(codes)}단지")
    got, hh_ok = 0, 0
    for c in codes:
        st, body = call(BASIS_API, ok_key, {"kaptCode": c, "_type": "json"})
        why = diagnose(body)
        if why:
            print(f"   ✗ {c}: {why}")
            print("   → 15058453(공동주택 기본 정보제공 서비스) 활용신청이 따로 필요하다")
            return 1
        nm = _re.search(r'"kaptName"\s*:\s*"([^"]+)"', body)
        hh = _re.search(r'"kaptdaCnt"\s*:\s*"?([\d.]+)', body)
        ad = _re.search(r'"doroJuso"\s*:\s*"([^"]+)"', body)
        if not nm:
            print(f"   ? {c}: 응답에 kaptName 이 없다 — {body[:120]}")
            continue
        got += 1
        n = int(float(hh.group(1))) if hh else 0
        hh_ok += n > 0
        print(f"   {'✅' if n else '⚠️ '} {nm.group(1)[:20]:22s} {n:6,}세대  "
              f"{(ad.group(1) if ad else '(도로명주소 없음)')[:34]}")

    if not got:
        print("\n❌ 기본정보 응답을 못 받았다")
        return 1
    print(f"\n   세대수 있음 {hh_ok}/{got} · 나머지는 0(소규모·오피스텔형에서 실제로 나온다)")
    print("\n🎉 키 정상. K-apt 수집기를 붙일 수 있다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
