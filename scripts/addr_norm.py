# -*- coding: utf-8 -*-
"""
addr_norm.py — 경쟁사 주소 정제 (공용 모듈)

경쟁사 주소는 **원천마다 표기가 제각각**이라 `geocode_addr.clean_addr()` 만으로는
부족하다. 실측된 표기 흔들림 (2026-07 xlsx 458건):

    서울 강동구 양재대로 1636, 7층 와이키즈 강동      쉼표 뒤 층 + 상호명
    강원 강릉시 솔올로 93 정도빌딩 4층 강릉교동소마     건물명 + 층 + 상호명
    서울 강서구 마곡동 739-1 마곡더블유타워 5층        지번 주소
    서울특별시 양천구 신정동 신목로 59, 2층            법정동 + 도로명 혼재
    경기도 성남시 분당구 정자로 80, 정자프라자5층       시도 풀네임

그래서 **후보를 여러 개 만들어 순서대로 지오코딩**한다. 하나라도 맞으면 통과.
정제를 과하게 하면 도로명 자체가 날아가므로, 후보는 "덜 깎은 것 → 더 깎은 것"
순으로 둔다.

⚠️ 시도 표기를 통일하되 **광주·전남을 `전남광주` 로 바꾸지 말 것.**
   2026-07-01 통합은 행정동 판정(좌표 PIP)이 처리한다. 주소 문자열은
   SGIS 가 아는 구 표기 그대로 넘겨야 지오코딩이 성공한다
   (`geocode_addr.py --selftest` 의 광주·전남 2건이 이 경로로 통과한다).
"""

from __future__ import annotations

import re

# 시도 풀네임 → 축약 (SGIS 는 양쪽 다 받지만 캐시 키를 하나로 모은다)
SIDO_FULL = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
    "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
    "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기",
    "강원특별자치도": "강원", "강원도": "강원",
    "충청북도": "충북", "충청남도": "충남",
    "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "제주도": "제주",
    "전남광주특별시": "전남광주", "전남광주통합특별시": "전남광주",
}

_POST = re.compile(r"^\s*[\(\[]?\d{5}[\)\]]?\s+")          # 우편번호
_PAREN = re.compile(r"\([^)]*\)")                          # (역삼동, OO빌딩)
_FLOOR_ANY = re.compile(r"\s*,?\s*(지하\s*)?[BbGg]?\d+\s*층.*$")   # 층 이후 전부
_HO_ANY = re.compile(r"\s*,?\s*\d+\s*호.*$")               # 호 이후 전부
_GWAN = re.compile(r"\s*\d+\s*관\s*$")                     # 1관 / 2관

# 도로명 + 건물번호 : '양재대로 1636' · '도곡로99길 16' · '고무래로 10길 17'
_ROAD = re.compile(
    r"([가-힣A-Za-z0-9]+(?:대로|로|길))\s*(\d+길)?\s*(\d+(?:-\d+)?)(?!\s*[가-힣]*[동로길])"
)
# 지번 : '마곡동 739-1' · '신도림동 437-1'
_JIBUN = re.compile(r"([가-힣]+(?:동|리|가))\s+(산\s*)?(\d+(?:-\d+)?)")

# 도로명 앞에 끼어든 법정동 : '양천구 신정동 신목로 59' → 신정동 제거
_BJD_BEFORE_ROAD = re.compile(
    r"([가-힣]+[시군구])\s+([가-힣]+(?:동|리|가))\s+(?=[가-힣A-Za-z0-9]+(?:대로|로|길)\s*\d)"
)


# --------------------------------------------------------------------------
# 수기 보정표
#
# 원천 주소 자체가 틀려 어떤 정제로도 지오코딩이 안 되는 건만 등재한다.
# `geo_legacy_map.MANUAL_ADM` 과 같은 취지 — **자동 추론 금지, 확인분만 등재.**
# 좌우를 자동으로 맞출 수 없으므로 근거를 주석으로 남긴다.
#
# ⚠️ 여기에 넣기 전에 "원본을 고칠 수 있는가"를 먼저 볼 것.
#    xlsx 는 관리자 자료라 고칠 수 있지만, 크롤 결과는 사이트가 원천이라 못 고친다.
# --------------------------------------------------------------------------

MANUAL_FIX = {
    # 정확 매칭 보정 (주소 전문이 고정일 때만)

    # 와이즈만 울산중구 — 도로명이 괄호 안에 있어 괄호 제거 후 `반구동` 만 남는다.
    # 그대로면 동 대표점(반구1동)에 찍혀 실제 위치(반구2동)와 ~1km 어긋났다.
    "울산 중구 반구동 (번영로 536 3동) 울산중구 와이즈만": "울산광역시 중구 번영로 536",
}

# 접두 보정 — **도로명 단위**로 고친다.
# 정확 매칭으로 두면 건물번호가 바뀌는 순간(크롤은 매월 새로 받는다) 다시 실패한다.
MANUAL_FIX_PREFIX = {
    # 필즈더클래식 인천/송도센터 — 사이트 원문에 **시군구가 없다**(시도 + 도로명).
    # 신송로는 인천 연수구 송도동 (같은 도로의 소마 인천송도점이 '인천 연수구 신송로 161').
    # 크롤 원천이라 우리가 고칠 수 없다 → 여기서 보정한다.
    "인천광역시 신송로": "인천 연수구 신송로",
    "인천 신송로": "인천 연수구 신송로",

    # 와이즈만 부산정관 — 원본이 **폐지된 행정구역 표기**다.
    # 기장군 정관면은 2015-09 정관읍으로 승격했고 SGIS 는 '정관면'을 모른다
    # ('정관읍 매학리 713-4' 는 정상 조회된다). 행정구역 개편이라 자동 추론이 아니다.
    "부산 기장군 정관면": "부산 기장군 정관읍",
    "부산광역시 기장군 정관면": "부산 기장군 정관읍",

    # 와이즈만 인천검단 — SGIS 가 신규 개발지 지번 `원당동 1030-5` 를 모른다.
    # 본번 폴백(`원당동 1030`)은 동은 맞지만 **옆 건물 JS프라자(이음5로 60)** 에 찍혔다.
    # JUSO 확인(2026-09-17): 원당동 1030-5 = 이음5로 70 새샘프라자.
    "인천 서구 원당동1030-5": "인천 서구 이음5로 70",
}


def missing_sgg(a: str) -> bool:
    """시도 다음 토큰이 시/군/구가 아니면 시군구 누락 — 지오코딩이 거의 실패한다.

    ⚠️ **세종시는 예외다.** 단층제라 시군구가 없는 게 정상이고
       ('세종 고운동 1720'), 이걸 경고에 넣으면 세종 지점 전부가 오탐으로 뜬다.
    """
    t = normalize_sido(a).split()
    if len(t) < 2 or t[0] in ("세종",):
        return False
    if t[0] not in set(SIDO_FULL.values()) | {"서울"}:
        return False
    return not re.search(r"[시군구]$", t[1])


def normalize_sido(a: str) -> str:
    a = (a or "").strip()
    for full, short in SIDO_FULL.items():
        if a.startswith(full):
            return short + a[len(full):]
    return a


# 지번이 동 이름에 붙어 있는 표기 — '원당동1030-5' · '마곡동739-1'.
# 띄어쓰기가 없으면 `_JIBUN` 이 지번을 못 떼어내 후보가 통째로 원문 하나가 된다.
#
# ⚠️ 숫자 **뒤**까지 봐야 한다. `당산동5가` · `금호동1가` · `동소문동4가` 는
#    숫자가 법정동 이름의 일부다. 뒤를 안 보고 벌리면 `당산동 5가` 가 되어
#    멀쩡하던 4건이 통째로 지오코딩에 실패했다(2026-08-03 실제로 겪음).
#    → 숫자 다음이 한글·숫자면 손대지 않는다.
# ⚠️ '능라동길27' 처럼 도로명 중간의 '동' 은 뒤가 '길'이라 애초에 안 걸린다.
_BJD_GLUED = re.compile(r"([가-힣]{2,}(?:동|리|가))(\d+(?:-\d+)?)(?![가-힣0-9])")


def _base(a: str) -> str:
    """공통 전처리 — 우편번호·괄호·중복공백·붙어쓴 지번."""
    a = normalize_sido(a)
    a = _POST.sub("", a)
    a = _PAREN.sub(" ", a)
    a = _BJD_GLUED.sub(r"\1 \2", a)
    return re.sub(r"\s+", " ", a).strip(" ,")


def road_part(a: str) -> str | None:
    """도로명+건물번호까지만 남긴다. 뒤의 건물명·층·상호는 버린다."""
    m = None
    for m in _ROAD.finditer(a):
        pass                                   # 마지막 매치가 실제 건물번호다
    return a[: m.end()].strip(" ,") if m else None


def jibun_part(a: str) -> str | None:
    m = None
    for m in _JIBUN.finditer(a):
        pass
    return a[: m.end()].strip(" ,") if m else None


def apply_fix(raw: str) -> str:
    """수기 보정표(MANUAL_FIX → MANUAL_FIX_PREFIX)를 적용한 주소."""
    r0 = (raw or "").strip()
    work = MANUAL_FIX.get(r0)
    if not work:
        for pre, rep in MANUAL_FIX_PREFIX.items():
            if r0.startswith(pre):
                return rep + r0[len(pre):]
    return work or r0


# 정밀도 검사용 번호 — `_ROAD`·`_JIBUN` 보다 느슨하다(괄호 안·붙여 쓴 `원당동1030-5` 도 잡는다)
_NUM_ROAD = re.compile(r"(?:로|길)\s*(?:\d+(?:번[가-힣]*)?길)?\s*(\d+(?:-\d+)?)")
_NUM_JIBUN = re.compile(r"(?:동|리|가)\s*(?:산\s*)?(\d+(?:-\d+)?)")


def lost_number(raw: str, used: str) -> str | None:
    """원본의 건물번호·지번이 **좌표를 찾은 후보에서 사라졌으면** 그 번호를 돌려준다.

    후보를 깎다 보면 좌표는 나오는데 **다른 건물**에 찍힌다. 에러도 실패율도 안 올라서
    지도를 눈으로 봐야만 드러났다(2026-09-17 실측 2건):
      와이즈만 인천검단  `원당동1030-5` → `원당동 1030`   옆 건물 JS프라자 (~120m)
      와이즈만 울산중구  `(번영로 536)` → `반구동`        동 대표점, 행정동까지 틀림 (~1km)
    도로명 번호와 지번 중 **하나라도** 남아 있으면 통과다(둘 다 적힌 주소는 한쪽만 써도 정확).
    """
    fixed = apply_fix(raw)
    nums = [m.group(1) for m in (_NUM_ROAD.search(fixed), _NUM_JIBUN.search(fixed)) if m]
    if not nums or any(re.search(rf"(?<![\d-]){re.escape(n)}(?![\d-])", used) for n in nums):
        return None
    return "/".join(nums)


def candidates(raw: str) -> list[str]:
    """지오코딩 후보를 '덜 깎은 것 → 더 깎은 것' 순으로 만든다 (중복 제거)."""
    out: list[str] = []

    # 수기 보정을 **먼저** 적용하고, 그 결과로 후보를 만든다.
    # (보정본만 후보에 얹으면 층·호가 남아 그것도 실패한다)
    work = apply_fix(raw)

    def add(x: str | None):
        if x:
            x = re.sub(r"\s+", " ", x).strip(" ,·-")
            # 시도+시군구만 남은 껍데기는 동 판정을 못 하니 후보에서 뺀다
            if len(x) >= 8 and x not in out:
                out.append(x)

    b = _base(work)
    add(b)

    # 층·호·관 제거
    c = _GWAN.sub("", _HO_ANY.sub("", _FLOOR_ANY.sub("", b))).strip(" ,")
    add(c)

    # 쉼표 앞까지
    if "," in c:
        add(c.split(",")[0])

    # 도로명까지만
    r = road_part(c)
    add(r)

    # 도로명 앞 법정동 제거판 ('양천구 신정동 신목로 59' → '양천구 신목로 59')
    if r and _BJD_BEFORE_ROAD.search(r):
        add(_BJD_BEFORE_ROAD.sub(r"\1 ", r))

    # 지번까지만 (도로명이 없는 원천용)
    j = jibun_part(c)
    add(j)

    # 마지막 후보 — 부번을 떼고 본번만.
    # SGIS 가 신규 개발지의 부번을 아직 모르는 경우가 있다
    # (와이즈만 인천검단 `원당동 1030-5` 는 실패하는데 `원당동 1030` 은 아라동으로 잡힌다).
    # 부번은 같은 블록이라 **행정동은 바뀌지 않는다** — 그래도 가장 많이 깎은 후보이므로
    # 앞 후보가 전부 실패했을 때만 쓰인다.
    # ⚠️ 좌표는 **다른 건물**에 찍힌다(검단 사례 ~120m). build_competitor 가 `lost_number()` 로
    #    막는다 — 걸리면 JUSO 로 도로명을 확인해 MANUAL_FIX(_PREFIX) 에 넣을 것.
    if j:
        add(re.sub(r"-\d+$", "", j))

    return out


def sido_of(a: str) -> str:
    a = normalize_sido(a)
    return a.split()[0] if a.strip() else ""


_F_TOK = re.compile(r"(?:지하\s*)?(\d+)\s*(?:층|[Ff](?![A-Za-z]))")
_H_TOK = re.compile(r"(\d+)\s*호")


def unit_key(a: str) -> str:
    """층·호를 표기 무관하게 정규화한다 ('2층' == '2F')."""
    f = sorted(set(_F_TOK.findall(a)), key=int)
    h = sorted(set(_H_TOK.findall(a)), key=int)
    return "F" + ",".join(f) + "H" + ",".join(h)


def dedupe_key(raw: str) -> str:
    """같은 지점을 두 번 등록한 것을 잡아내는 키.

    원본 xlsx 에 실제로 있었다 — `판교공원로1길 1 ... 2층` 과 `... 2F` 가
    별도 행으로 들어와 지도에 경쟁사가 하나 더 있는 것처럼 보였다.

    ⚠️ **건물 단위로만 비교하면 안 된다.** 같은 건물에 별개 지점이 실재한다:
         소마 김포한강 1관(211호) / 2관(301호)
         폴리 김포캔비GRAD(2층 205호) / 김포(601호)
       그래서 키 = 도로명+건물번호 **+ 정규화된 층·호** 다.
       층·호가 양쪽 다 없으면 구분할 근거가 없어 병합되는데, 그건 수용한다.
    """
    b = _base(raw)
    c = _GWAN.sub("", _HO_ANY.sub("", _FLOOR_ANY.sub("", b))).strip(" ,")
    base = (road_part(c) or jibun_part(c) or c).replace(" ", "")
    return base + "|" + unit_key(b)


if __name__ == "__main__":
    # 자체 점검: python scripts/addr_norm.py  (lost_number 규칙이 깨지면 AssertionError)
    L = lost_number
    assert L("인천 서구 원당동1030-7 서영아너시티2차 3층", "인천 서구 원당동 1030") == "1030-7"   # 본번 폴백
    assert L("울산 중구 반구동 (번영로 536 3동) 울산중구 와이즈만", "울산 중구 반구동 울산중구 와이즈만") == "536"
    assert L("서울 관악구 봉천동 874-6 호혜빌딩 8층(남부순환로 1797)", "서울 관악구 봉천동 874-6 호혜빌딩 8층") is None
    assert L("경기 광명시 하안로288번길 15 조일프라자 3층", "경기 광명시 하안로288번길 15") is None
    assert L("세종 고운동 산 167번지 (세종로 1229) 스타플레이스 5층", "세종 고운동 산 167번지") is None
    assert L("인천 서구 원당동1030-5 새샘프라자", "인천 서구 이음5로 70") is None    # MANUAL_FIX_PREFIX 반영
    print("addr_norm selftest OK")
