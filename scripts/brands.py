# -*- coding: utf-8 -*-
"""
brands.py — 자사·경쟁사·시너지세권 브랜드 레지스트리 (공용 모듈)

`load_competitor.py`(xlsx 로더) · `crawl_competitor.py`(월 갱신 크롤러) ·
`build_competitor.py`(지오코딩·집계) 가 **이 파일 하나를 SSOT 로 공유**한다.
브랜드를 추가할 때는 여기만 고치면 세 스크립트가 함께 따라온다.

카테고리 (DATA_CONTRACT §2-10)
  own             자사 센터            — 지도에 "우리 위치"로 표시
  competitor      경쟁사               — 같은 수요를 두고 다투는 브랜드. **많을수록 불리**
  english_kinder  **시너지세권**        — 영어유치원 위치. **경쟁사가 아니다.**
                                        유아 사교육 수요가 모이는 자리라
                                        **근처 입점이 유리**하다는 신호 (많을수록 유리)

⚠️ **표시명은 `시너지세권` 이다** (2026-08-03 관리자 명명).
   `english_kinder` 는 **JSON 키로만 남긴다** — 이미 `poi_*.json`(256개) ·
   `competitor_adm/sgg/sido.json` · `competitor_history.json` 에 박혀 있어서
   키를 바꾸면 전부 재생성해야 하고, 옛 산출물과 조인이 조용히 끊긴다.
   **사람이 보는 문구는 이 파일의 `CATEGORIES` 가 SSOT** 다.

⚠️ **시너지세권을 경쟁사와 합산하지 말 것** (2026-08-03 관리자 확인).
   폴리·라이즈를 넣은 이유는 "여기 경쟁이 심하다"가 아니라 **"여기 붙으면 좋다"** 다.
   합산하면 좋은 자리가 나쁜 자리로 뒤집혀 읽힌다 — 상담 자료에서 방향이 반대인 오류다.
   (대시보드의 `rival_cnt` = 경쟁사+영유 합산 필드는 이 이유로 삭제했다)

⚠️ `brand_target` 은 **배열**이다.
   2026-08-03 와이즈만 축이 들어오면서 타깃이 두 개(`와이키즈`·`와이즈만`)가 됐다.
   지금은 브랜드마다 타깃이 하나씩이지만, 한 브랜드가 두 타깃에 걸릴 수 있다.
   **단일 문자열로 되돌리지 말 것** — 되돌리면 그때 스키마·집계·화면을 전부 고쳐야 한다.

⚠️ 자사(`own`)가 **두 브랜드**다(와이키즈·와이즈만).
   대시보드의 `자사 센터 수` 는 둘을 **합산**하고, 브랜드별 분포 막대가
   자사를 강조할 때도 `own` 전체를 본다(`build_competitor` 가 `meta.own_brands` 로 넘긴다).
   "자사 = 와이키즈" 로 하드코딩된 코드가 남아 있으면 와이즈만이 경쟁사처럼 보인다.

⚠️ 브랜드를 추가·삭제하면 `min_expect` 를 반드시 같이 손볼 것.
   크롤러의 유일한 "조용한 실패" 방어선이다 (§3 참조).
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 브랜드 정의
#
#   key          내부 식별자 (id prefix·파일명에 쓰인다. 바꾸면 first_seen 이 끊긴다)
#   name         표시명 (xlsx 시트의 `브랜드명` 과 일치해야 로더가 붙는다)
#   category     own / competitor / english_kinder
#   brand_target 어느 자사 브랜드의 경쟁사인가 (배열)
#   url          월 갱신 크롤링 대상. own 은 None (자사는 내부 자료가 정본)
#   min_expect   크롤 결과가 이 건수 미만이면 **실패로 간주하고 중단**한다.
#                2026-07 실측치의 약 70% 로 잡았다 — 사이트 개편으로 셀렉터가
#                깨지면 0건이나 극소수가 조용히 내려오는데, 그게 지도에서는
#                "경쟁사 없는 좋은 상권"으로 보인다. 가장 위험한 방향의 오류다.
#   aliases      xlsx 시트명·표기 흔들림 흡수 (필즈더클래식 vs 필드더클랙식)
# --------------------------------------------------------------------------

BRANDS = [
    {
        "key": "waikids",
        "name": "와이키즈",
        "category": "own",
        "brand_target": ["와이키즈"],
        "url": None,
        "min_expect": 40,
        "aliases": ["와이키즈", "Y-KIDS", "와이키즈수학"],
    },
    {
        # 2026-08-03 관리자 제공 `와이즈만 위치.xlsx` (95지점, 시트명 `와이즈만`).
        # ⚠️ 같은 날 먼저 받은 `와이즈만 주소.xlsx`(73건)는 **부분 명부**다
        #    (인천·광주·전북·충남·전남·울산·세종·제주가 통째로 없고 서울이 '마포'에서 끊긴다).
        #    자사 센터가 빠지면 지도에서 "우리 없는 지역"이 되므로 그 파일은 쓰지 말 것.
        "key": "wiseman",
        "name": "와이즈만",
        "category": "own",
        "brand_target": ["와이즈만"],
        "url": None,
        "min_expect": 66,                       # 95의 약 70%
        "aliases": ["와이즈만", "와이즈만영재교육", "WISEMAN", "와이즈만CNI"],
    },
    {
        "key": "soma",
        "name": "소마",
        "category": "competitor",
        "brand_target": ["와이키즈"],
        "url": "https://new.somai.co.kr/brand/map",
        "min_expect": 120,
        "aliases": ["소마", "소마셈", "소마사고력수학"],
    },
    {
        "key": "adamriz",
        "name": "아담리즈",
        "category": "competitor",
        "brand_target": ["와이키즈"],
        "url": "https://www.mathplay.co.kr/center/",
        "min_expect": 50,
        "aliases": ["아담리즈", "아담리즈수학", "매스플레이"],
    },
    {
        "key": "fields",
        "name": "필즈더클래식",
        "category": "competitor",
        "brand_target": ["와이키즈"],
        "url": "https://fieldsgood.co.kr/sub/branchcampus.php",
        "min_expect": 35,
        "aliases": ["필즈더클래식", "필드더클랙식", "필즈", "FIELDS"],
    },
    {
        "key": "poly",
        "name": "폴리어학원",
        "category": "english_kinder",
        "brand_target": ["와이키즈"],
        "url": "https://www.poly-english.com/aboutpoly/campusList.do",
        "min_expect": 48,
        "aliases": ["폴리어학원", "폴리", "POLY"],
    },
    {
        "key": "rise",
        "name": "라이즈어학원",
        "category": "english_kinder",
        "brand_target": ["와이키즈"],
        "url": "https://www.risekorea.com/find/find__list",
        "min_expect": 27,
        "aliases": ["라이즈어학원", "라이즈", "RISE"],
    },
    {
        # 2026-08-03 관리자 지정 — **와이즈만 경쟁사는 이 브랜드 하나뿐이다.**
        # 소마·아담리즈·필즈·폴리·라이즈는 와이키즈 전용으로 둔다(관리자 확인).
        "key": "bull",
        "name": "생각하는황소",
        "category": "competitor",
        "brand_target": ["와이즈만"],
        "url": "https://www.thinking-bull.com/branch",
        "min_expect": 58,                       # 83의 약 70%
        "aliases": ["생각하는황소", "황소", "황소수학", "thinkingbull"],
    },
    # ── 2026-09-17 관리자 추가 5개 사이트 (6개 브랜드) ────────────────────────
    # 관리자: "경쟁사는 브랜드별로 묶을 필요 없어 그냥 경쟁사로 배정"
    #   → 특정 자사에 귀속하지 않는다는 뜻으로 두 자사를 다 넣었다.
    # CMS 는 크레버스 통합 사이트에서 **CMS영재교육센터(MT)·CMS영재관(MG) 두 브랜드만** 받는다
    # (청담·April·iGARTEN·코딩은 제외, 관리자 지정). 두 브랜드는 분리 집계(관리자 결정).
    # min_expect = 2026-09-17 실측의 약 70%.
    {
        "key": "cmath",
        "name": "시매쓰",
        "category": "competitor",
        "brand_target": ["와이키즈", "와이즈만"],
        "url": "https://cmath.co.kr/aca/center_list.asp",
        "min_expect": 105,                      # 150의 약 70%
        "aliases": ["시매쓰", "시매쓰수학", "CMATH"],
    },
    {
        "key": "cms_mt",
        "name": "CMS영재교육센터",
        "category": "competitor",
        "brand_target": ["와이키즈", "와이즈만"],
        "url": "https://creverse.com/admission/",
        "min_expect": 50,                       # 72의 약 70%
        "aliases": ["CMS영재교육센터", "CMS사고력관", "CMS"],
    },
    {
        "key": "cms_mg",
        "name": "CMS영재관",
        "category": "competitor",
        "brand_target": ["와이키즈", "와이즈만"],
        "url": "https://creverse.com/admission/",
        "min_expect": 9,                        # 13의 약 70%
        "aliases": ["CMS영재관"],
    },
    {
        "key": "brain",
        "name": "브레인스쿨",
        "category": "competitor",
        "brand_target": ["와이키즈", "와이즈만"],
        "url": "https://www.brainschool.co.kr/inquiry/center.do",
        "min_expect": 19,                       # 28의 약 70%
        "aliases": ["브레인스쿨", "BRAINSCHOOL"],
    },
    {
        "key": "hlsi",
        "name": "한생연",
        "category": "competitor",
        "brand_target": ["와이키즈", "와이즈만"],
        "url": "https://hlsi.co.kr/sub/sub02_01.php",
        "min_expect": 5,                        # 8의 약 70%
        "aliases": ["한생연", "한국생명과학연구원"],
    },
    {
        "key": "u2m",
        "name": "유투엠",
        "category": "competitor",
        "brand_target": ["와이키즈", "와이즈만"],
        "url": "https://www.u2math.co.kr/Campus/List?idx=113",
        "min_expect": 82,                       # 116(주소 있는 건)의 약 70%
        "aliases": ["유투엠", "U2M", "유투엠수학"],
    },
]

# ── 조회 인덱스 ────────────────────────────────────────────────────────────
BY_KEY = {b["key"]: b for b in BRANDS}
BY_NAME = {b["name"]: b for b in BRANDS}

_ALIAS = {}
for _b in BRANDS:
    for _a in _b["aliases"]:
        _ALIAS[_a.replace(" ", "").lower()] = _b

# 화면·로그에 나가는 **표시명 SSOT**. (JSON 키는 바꾸지 않는다 — 위 ⚠️ 참조)
CATEGORIES = {
    "own": "자사 센터",
    "competitor": "경쟁사",
    "english_kinder": "시너지세권",
}

# 무엇으로 이루어졌는지 — "시너지세권이 뭐냐"는 질문에 답하는 한 줄
CATEGORY_SOURCE = {
    "english_kinder": "영어유치원 브랜드 위치 (폴리·라이즈)",
}

# 카테고리의 **의미** — 화면 문구와 "합산해도 되는가"가 여기서 갈린다.
# `build_competitor.py` 가 `meta.category_role` 로 내보내 대시보드·문서가 같은 말을 쓰게 한다.
CATEGORY_ROLE = {
    "own": "자사 — 우리 센터",
    "competitor": "경쟁 — 같은 수요를 두고 다툰다. 많을수록 불리",
    "english_kinder": "시너지 — 경쟁사가 아니다. 영어유치원 인근은 유아 사교육 수요가 모여 "
                      "자사 입점이 유리하다. 많을수록 유리",
}

# ---------------------------------------------------------------------------
# 자료 대기 중인 브랜드 (자리만 잡아 둔다)
#
# 여기에 적힌 것은 **아직 데이터가 없다.** 자료를 받으면 위 BRANDS 에 같은 값으로
# 등재하고 이 목록에서 지우면 된다 — 나머지 스크립트·화면은 손댈 필요 없다.
# 대기 상태를 코드에 남겨 두는 이유는, 지도에 "영유 0개"로 보이는 지역이
# **정말 없는 것인지 아직 안 넣은 것인지** 구분이 안 되기 때문이다.
# `build_competitor.py` 가 매 회차 이 목록을 출력하고 `meta.pending` 에도 남긴다.
# ---------------------------------------------------------------------------
PENDING = [
    {
        "key": "wiseman_kinder",            # 확정 시 실제 브랜드 key 로 바꿀 것
        "name": "(미정) 와이즈만 시너지세권",
        "category": "english_kinder",
        "brand_target": ["와이즈만"],
        "note": "관리자 자료 대기 (2026-08-03 요청). 와이키즈의 폴리·라이즈에 해당하는 시너지세권 축",
    },
]


def resolve(name: str) -> dict | None:
    """시트명·표기 흔들림을 흡수해 브랜드 정의를 찾는다."""
    if not name:
        return None
    return _ALIAS.get(str(name).replace(" ", "").lower())


def crawlable() -> list[dict]:
    """월 갱신 크롤링 대상 (자사 제외)."""
    return [b for b in BRANDS if b["url"]]


def of_category(cat: str) -> list[dict]:
    return [b for b in BRANDS if b["category"] == cat]
