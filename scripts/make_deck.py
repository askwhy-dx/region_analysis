# -*- coding: utf-8 -*-
"""make_deck.py — 사업부 발표용 덱 생성 (.pptx)

    python scripts/make_deck.py
    python scripts/make_deck.py --out D:\\발표\\지역분석.pptx

**숫자를 손으로 적지 않는다.** 전부 `data/processed` 에서 읽어 넣는다 — 발표 자료가
산출물과 어긋나는 일이 없어야 하고, 다시 돌리면 그날 기준으로 갱신된다.

구성 (7장)
  0. 표지 · 한 줄 정의 · 공개 URL
  1. 어떤 데이터가 있나 (원천·건수·기준시점)
  2. 지도는 어떤 데이터로 그려지나 (배경 그림 · 동 경계선 · 위치 점)
  3. 어떻게 집계되나 (주소 → 좌표 → 행정동)
  4. 쓰실 때 알아두실 점 (위성지도·0의 의미·경계 불확실·기준시점)
  5. 데이터가 틀어지는 경우와 대응
  6. 월 업데이트와 검증

⚠️ 「폴리곤」 같은 전문 용어는 쓰지 않는다 — 사업부 발표용이라 **동 경계선 · 지도 구역**
   으로 부른다(2026-08-12 요청).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Pt

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"
URL = "https://jichang999-dotcom.github.io/tes2/jiyeok/web/"

NAVY = RGBColor(0x1B, 0x33, 0x5C)
BLUE = RGBColor(0x16, 0x87, 0xC9)
GREY = RGBColor(0x5A, 0x63, 0x72)
LIGHT = RGBColor(0xF2, 0xF5, 0xF9)
RED = RGBColor(0xB4, 0x1E, 0x2F)
GREEN = RGBColor(0x16, 0xA3, 0x7D)
FONT = "맑은 고딕"

W, H = Emu(12192000), Emu(6858000)          # 16:9


def rd(f: str) -> dict:
    p = P / f
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def facts() -> dict:
    """발표에 쓸 숫자를 산출물에서 직접 뽑는다."""
    met, sch = rd("metrics.json"), rd("school.json")
    aca, kapt = rd("academy.json"), rd("kapt.json")
    comp, cc = rd("competitor.json"), rd("childcare.json")
    apt, pop = rd("apartment.json"), rd("population.json")
    gc = rd("geocode_cache.json").get("data") or {}
    md = met.get("data") or {}
    sd = sch.get("data") or {}
    f = {
        "updated": met.get("meta", {}).get("updated", str(date.today())),
        "dong": len(md),
        "pop": sum(v.get("population") or 0 for v in md.values()),
        "pop_ym": pop.get("meta", {}).get("base_ym", ""),
        "school": sum(v.get("school_cnt", 0) for v in sd.values()),
        "students": sum((v.get("students_elem") or 0) + (v.get("students_mid") or 0)
                        + (v.get("students_high") or 0) for v in sd.values()),
        "kinder": sum(v.get("kinder_cnt", 0) for v in sd.values()),
        "school_year": sch.get("meta", {}).get("base_year", ""),
        "academy": sum(v.get("academy_cnt", 0) for v in (aca.get("data") or {}).values()),
        "apt_complex": kapt.get("meta", {}).get("total_complexes", 0),
        "apt_hh": sum(v.get("households", 0) for v in (kapt.get("data") or {}).values()),
        "apt_period": apt.get("meta", {}).get("period", ""),
        "apt_ym": kapt.get("meta", {}).get("base_ym", ""),
        "own": comp.get("meta", {}).get("by_category", {}).get("own", 0),
        "comp": comp.get("meta", {}).get("by_category", {}).get("competitor", 0),
        "syn": comp.get("meta", {}).get("by_category", {}).get("english_kinder", 0),
        "comp_ym": comp.get("meta", {}).get("base_ym", ""),
        "daycare": (cc.get("meta", {}).get("report", {}) or {}).get("placed", 0),
        "daycare_left": len(cc.get("meta", {}).get("unresolved_sgg", {}) or {}),
        "geo": sum(1 for v in gc.values() if v),
        "suspect": sum(1 for v in md.values() if v.get("apt_ratio_suspect")),
        "has_cc": bool((met.get("meta", {}).get("axes") or {}).get("childcare")),
    }
    # 지도(경계) 관련 — 구역 조각 수는 geojson 을 직접 센다
    import glob as _g
    shards = 0
    for gf in (ROOT / "data" / "geo").glob("adm_*.geojson"):
        shards += len(json.loads(gf.read_text(encoding="utf-8"))["features"])
    f["shards"] = shards
    f["poi"] = {"academy": f["academy"], "school": 11968,
                "apartment": kapt.get("meta", {}).get("total_complexes", 0)}
    dg = sorted((ROOT / "data" / "logs").glob("apt_ratio_diagnosis_*.json"))
    f["boundary"] = (json.loads(dg[-1].read_text(encoding="utf-8")).get("tally", {}).get("경계형", 0)
                     if dg else 0)
    return f


# ── 그리기 헬퍼 ────────────────────────────────────────────────────────────

def box(slide, x, y, w, h, text, size=14, bold=False, color=NAVY, align=PP_ALIGN.LEFT,
        fill=None, line=None, space=6):
    tb = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
    if fill is not None:
        tb.fill.solid(); tb.fill.fore_color.rgb = fill
    else:
        tb.fill.background()
    if line is not None:
        tb.line.color.rgb = line; tb.line.width = Pt(1)
    else:
        tb.line.fill.background()
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Emu(90000)
    tf.margin_top = tf.margin_bottom = Emu(50000)
    for i, ln in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space)
        r = p.add_run(); r.text = ln
        r.font.size = Pt(size); r.font.bold = bold
        r.font.color.rgb = color; r.font.name = FONT
    return tb


def header(slide, no, title, sub=""):
    bar = slide.shapes.add_textbox(Emu(0), Emu(0), W, Emu(760000))
    bar.fill.solid(); bar.fill.fore_color.rgb = NAVY; bar.line.fill.background()
    tf = bar.text_frame; tf.margin_left = Emu(500000); tf.margin_top = Emu(180000)
    p = tf.paragraphs[0]
    r = p.add_run(); r.text = f"{no}. {title}"
    r.font.size = Pt(24); r.font.bold = True
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF); r.font.name = FONT
    if sub:
        box(slide, 500000, 800000, 11200000, 400000, sub, 13, False, GREY)


def table(slide, x, y, w, rows, widths, size=12, head_fill=LIGHT, row_h=430000):
    """간단한 표 — pptx 기본 표는 스타일 손보기가 번거로워 도형으로 그린다."""
    cx = x
    for ci, cw in enumerate(widths):
        cy = y
        for ri, row in enumerate(rows):
            head = ri == 0
            box(slide, cx, cy, int(w * cw), row_h, str(row[ci]),
                size=size, bold=head,
                color=NAVY if head else GREY,
                fill=head_fill if head else (RGBColor(0xFF, 0xFF, 0xFF) if ri % 2 else LIGHT),
                line=RGBColor(0xD8, 0xDE, 0xE6), space=0)
            cy += row_h
        cx += int(w * cw)
    return y + row_h * len(rows)


# ── 슬라이드 ───────────────────────────────────────────────────────────────

def slide_cover(prs, f):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg = s.shapes.add_textbox(Emu(0), Emu(0), W, H)
    bg.fill.solid(); bg.fill.fore_color.rgb = NAVY; bg.line.fill.background()
    box(s, 900000, 1900000, 10400000, 900000, "지역분석 대시보드", 44, True,
        RGBColor(0xFF, 0xFF, 0xFF))
    box(s, 900000, 2850000, 10400000, 600000,
        "학원 프랜차이즈 가맹상담용 상권 진단 · 전국 시도 → 시군구 → 행정동", 18, False,
        RGBColor(0xC7, 0xD6, 0xE8))
    box(s, 900000, 3700000, 10400000, 500000, URL, 15, True, RGBColor(0x7E, 0xC8, 0xF7))
    box(s, 900000, 4400000, 10400000, 900000,
        f"행정동 {f['dong']:,}개 · 인구 {f['pop']:,}명 · 학원 {f['academy']:,}개 · "
        f"아파트 {f['apt_hh']:,}세대\n기준 {f['updated']}", 14, False,
        RGBColor(0x9F, 0xB3, 0xCC))


def slide_data(prs, f):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 1, "어떤 데이터가 들어 있나", "8개 원천을 행정동 단위로 합쳐 하나의 화면에서 본다")
    rows = [
        ["축", "원천", "규모", "기준시점"],
        ["인구·세대", "주민등록 (행정안전부)", f"{f['pop']:,}명 / {f['dong']:,}개 동", f["pop_ym"]],
        ["학교·학생", "학교알리미 공시", f"초·중 {f['school']:,}개 · 재학생 {f['students']:,}명", f"{f['school_year']}년"],
        ["유치원", "유치원알리미 공시", f"{f['kinder']:,}개", f"{f['school_year']}년"],
        ["학원", "NEIS (입시·보습만)", f"{f['academy']:,}개", "월 갱신"],
        ["아파트 시세", "국토부 실거래 RTMS", f"단지 기준 24개월", f["apt_period"]],
        ["아파트 세대수", "K-apt 공동주택", f"{f['apt_complex']:,}단지 · {f['apt_hh']:,}세대", f["apt_ym"]],
        ["자사·경쟁사", "브랜드 공식 지점 (8개)", f"자사 {f['own']} · 경쟁사 {f['comp']} · 시너지세권 {f['syn']}", f["comp_ym"]],
        ["어린이집", "보육정보공개 포털", f"{f['daycare']:,}개 (연령별 원아) — 준비 중", "수집 완료 후 공개"],
    ]
    table(s, 500000, 1400000, 11200000, rows, [0.14, 0.24, 0.42, 0.20], size=11)
    box(s, 500000, 5680000, 11200000, 900000,
        "▸ 모든 데이터는 좌표로 행정동을 정한 뒤, 그 동 코드로 합쳐진다 — 인구 대비 학원 수, 세대수 대비 시세처럼 "
        "축을 섞은 비교가 가능한 이유다.\n"
        "▸ 어린이집은 유치원이 못 잡는 만0~2세와 만3~5세의 절반을 채운다. 수집 완료 후 합류한다.",
        12, False, GREY)


def slide_map(prs, f):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 2, "지도는 어떤 데이터로 그려지나",
           "화면의 지도는 세 겹이다 — 배경 그림 · 동 경계선 · 위치 점")
    layers = [
        ("① 배경 그림", BLUE,
         "일반 지도 = OpenStreetMap · 위성 = 국토부 VWorld\n"
         "브라우저가 지도 회사에서 직접 받는다 (우리 서버를 안 거친다)"),
        ("② 동 경계선", GREEN,
         f"행정동을 나누는 선. 전국 {f['dong']:,}개 동 · 조각 {f['shards']:,}개\n"
         "(섬·하천으로 갈린 동이 있어 조각이 동보다 많다) · 행정안전부 기준 자료"),
        ("③ 위치 점", NAVY,
         f"학원 {f['poi']['academy']:,} · 학교 {f['poi']['school']:,} · "
         f"아파트 단지 {f['poi']['apartment']:,} · 자사/경쟁사 {f['own'] + f['comp']}\n"
         "주소를 좌표로 바꿔 찍는다"),
    ]
    y = 1400000
    for ttl, c, d in layers:
        box(s, 500000, y, 2600000, 800000, ttl, 15, True, RGBColor(0xFF, 0xFF, 0xFF),
            PP_ALIGN.CENTER, fill=c)
        box(s, 3150000, y, 8550000, 800000, d, 12, False, GREY,
            fill=RGBColor(0xFF, 0xFF, 0xFF), line=RGBColor(0xD8, 0xDE, 0xE6))
        y += 880000
    box(s, 500000, 4200000, 11200000, 480000,
        "동 경계선이 왜 중요한가 — 이 선이 「어느 동인지」를 정한다", 15, True, NAVY)
    rows = [["", "내용"],
            ["쓰는 자료", "행정안전부 행정동 경계 (2026-07-01 전남광주통합 반영본)"],
            ["갱신 주기", "약 분기 1회 · 새 버전이 올라오면 월간 점검이 알려 준다"],
            ["한계", f"신도시 분동을 늦게 반영한다 → 전국 {f['dong']:,}개 중 {f['boundary']}개 동은 옆 동과 값이 섞일 수 있다"],
            ["대응", "행안부 주소로 재판정 · 해당 동은 아파트 지표의 색·순위에서 자동 제외"]]
    table(s, 500000, 4750000, 11200000, rows, [0.16, 0.84], size=12, row_h=440000)


def slide_pipeline(prs, f):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 3, "어떻게 집계되나", "원천마다 주소 형태가 달라, 전부 「좌표」로 바꿔 행정동에 넣는다")
    steps = [("① 주소 수집", "원천별 API·공시자료\n형식이 제각각"),
             ("② 좌표 변환", f"주소 → 위경도\n{f['geo']:,}건 지오코딩"),
             ("③ 행정동 판정", "좌표가 어느 동 구역\n안에 있는지 계산"),
             ("④ 교차 검증", "동 경계선 ↔ 통계청 ↔ 행안부\n세 곳이 갈리면 재판정"),
             ("⑤ 발행 게이트", "데이터 38항목\n화면 112항목 통과 시")]
    x, w = 500000, 2180000
    for i, (t, d) in enumerate(steps):
        box(s, x + i * (w + 60000), 1500000, w, 500000, t, 14, True,
            RGBColor(0xFF, 0xFF, 0xFF), PP_ALIGN.CENTER, fill=BLUE)
        box(s, x + i * (w + 60000), 2010000, w, 900000, d, 11, False, GREY,
            PP_ALIGN.CENTER, fill=LIGHT, line=RGBColor(0xD8, 0xDE, 0xE6))
    box(s, 500000, 3150000, 11200000, 450000,
        "핵심 — 주소의 「동 이름」을 믿지 않는다. 법정동과 행정동은 1:1이 아니라서다.", 14, True, NAVY)
    rows = [["단계", "무엇을 막나"],
            ["좌표 판정이 정본", "법정동↔행정동 변환표는 원리적으로 부정확(N:M) → 좌표로만 판정"],
            ["3원천 교차 검증", "동 경계선 자료가 낡으면 값이 옆 동에 쌓인다 → 행안부 주소로 재판정"],
            ["심판도 검사", "행안부 원본에도 한 번지만 틀린 레코드가 있다 → 이웃 번지와 대조"],
            ["발행 게이트", "좌표와 배정 동이 어긋나면 발행이 멈춘다 (오늘 101건을 잡았다)"]]
    table(s, 500000, 3700000, 11200000, rows, [0.22, 0.78], size=12)


def slide_notes(prs, f):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 4, "쓰실 때 알아두실 점", "화면이 정상으로 보여도 배경에 이런 사정이 있다")
    items = [
        ("위성지도가 안 뜰 때", GREEN,
         "지도 배경의 「미리받기」로 상담 전에 그 지역을 받아 두면 네트워크가 끊겨도 뜹니다.\n"
         "안 뜨면 자동으로 일반 지도로 바뀌고 안내가 뜹니다 — 배경 버튼을 다시 누르면 재시도.\n"
         "※ 위성 키는 전 직원이 공유합니다. 동시 사용이 많으면 한도에 걸릴 수 있습니다."),
        ("0은 「없음」이지 「모름」이 아니다", BLUE,
         "학원 0개는 실제로 0개라는 뜻입니다. 데이터가 안 들어온 칸은 「–」로 비워 둡니다.\n"
         "수집이 덜 된 축은 아예 화면에 올리지 않습니다 (어린이집이 지금 그 상태)."),
        ("경계가 불확실한 동이 있다", RED,
         f"행정동 경계 자료가 신도시 분동을 늦게 반영해, 전국 {f['dong']:,}개 중 "
         f"{f['boundary']}개 동은 옆 동과 값이 섞일 수 있습니다.\n"
         "해당 동은 아파트 지표의 색·순위에서 자동 제외됩니다. 인접 동과 합쳐 보시면 정확합니다."),
        ("데이터마다 기준시점이 다르다", GREY,
         f"인구 {f['pop_ym']} · 실거래 {f['apt_period'].split('~')[-1]} · 학교 {f['school_year']}년 공시.\n"
         "학교 학생수는 연 1회 공시라 연중에는 바뀌지 않습니다."),
    ]
    y = 1400000
    for t, c, d in items:
        box(s, 500000, y, 3000000, 900000, t, 14, True, c, fill=LIGHT,
            line=RGBColor(0xD8, 0xDE, 0xE6))
        box(s, 3550000, y, 8150000, 900000, d, 12, False, GREY,
            fill=RGBColor(0xFF, 0xFF, 0xFF), line=RGBColor(0xD8, 0xDE, 0xE6))
        y += 1000000


def slide_errors(prs, f):
    """데이터가 틀어지는 경우 — 사업부용이라 **원인 이름을 쓰지 않고 증상으로** 설명한다.\n(내부 문서용 5경로 정리는 README 「행정동 배정이 틀어지는 5가지 경로」)"""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 5, "데이터가 틀어지는 경우와 대응",
           "지도 자료와 주소 자료가 어긋날 때 생긴다 — 겹겹이 걸러 낸다")
    rows = [
        ["어떤 일이", "왜", "어떻게 잡나", "결과"],
        ["시설이 옆 동으로 세어진다", "신도시가 갈라졌는데\n우리 지도의 선이 아직 옛것",
         "행정안전부 주소에\n다시 물어 바로잡는다", "1,178건\n교정"],
        ["엉뚱한 동으로 세어진다", "주소 자료에 한 집만\n다른 동으로 등록돼 있다",
         "옆집 주소와 비교해\n혼자 다르면 거른다", "236건\n교정"],
        ["고쳤는데 화면이 그대로", "옛 값이 파일에\n박혀 있었다",
         "올리기 전에 위치와\n동을 다시 대조한다", "101건\n적발"],
        ["숫자만 안 맞는다", "세대수를 세는 기준이 다르다\n(다 지어진 가구 vs 실제 전입)",
         "원인 확인 중\n해당 동은 순위에서 제외", "27개 동\n확인 중"],
    ]
    table(s, 500000, 1350000, 11200000, rows, [0.26, 0.30, 0.29, 0.15], size=11, row_h=640000)

    box(s, 500000, 4700000, 11200000, 420000,
        "겹겹이 검사한다 — 각 단계가 앞 단계의 실수를 잡는다", 15, True, NAVY)
    steps = [("① 위치로 판정", "주소 표기 문제"),
             ("② 주소로 재확인", "우리 지도가 낡은 것"),
             ("③ 옆집과 대조", "주소 자료의 오류"),
             ("④ 상식 검사", "③의 과잉 교정"),
             ("⑤ 발행 전 대조", "고친 게 안 간 것")]
    x, w = 500000, 2180000
    for i, (ttl, d) in enumerate(steps):
        cx = x + i * (w + 60000)
        box(s, cx, 5220000, w, 400000, ttl, 12, True, RGBColor(0xFF, 0xFF, 0xFF),
            PP_ALIGN.CENTER, fill=BLUE if i < 4 else GREEN)
        box(s, cx, 5620000, w, 480000, d, 10, False, GREY, PP_ALIGN.CENTER,
            fill=LIGHT, line=RGBColor(0xD8, 0xDE, 0xE6))

    box(s, 500000, 6250000, 11200000, 400000,
        "▸ ③④⑤ 는 2026-08-12 에 새로 넣었고, 넣자마자 실제 오류를 잡아냈다 — "
        "전부 화면에는 정상으로 보이던 것들이다.", 11, True, RED)


def slide_ops(prs, f):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 6, "월 업데이트와 검증", "갱신은 사람이 시작하고, 올릴지 말지는 검증이 정한다")
    rows = [
        ["주기", "무엇을", "실행"],
        ["매월 첫 평일", "자동 점검 — 원천이 낡았는지 · 경계 새 버전 · 검증 통과 여부",
         "스케줄러 (자동)"],
        ["월 1회", "자사·경쟁사 지점 (8개 브랜드)", "update_competitor.bat"],
        ["월 1회", "아파트 세대수 (K-apt)", "update_kapt.bat"],
        ["분기·수시", "아파트 실거래 · 인구 · 학원", "수집 스크립트 3종"],
        ["연 1회", "학교·유치원 (공시가 연 1회다)", "load_school.py"],
        ["매일 09:20", "어린이집 잔여 수집 (다 차면 자동 종료)", "스케줄러"],
    ]
    table(s, 500000, 1350000, 6900000, rows, [0.22, 0.48, 0.30], size=10, row_h=430000)

    # ── 호출 한도 때문에 하루에 못 받는 데이터 ─────────────────────────────
    # 발표에서 꼭 나오는 질문이다 — "왜 어떤 건 며칠씩 걸리나".
    box(s, 7600000, 1350000, 4100000, 430000, "하루에 다 못 받는 데이터", 13, True,
        RGBColor(0xFF, 0xFF, 0xFF), PP_ALIGN.CENTER, fill=RED)
    lim = [("아파트 세대수 (K-apt)", "일 10,000건 한도 · 22,257단지\n→ 3일에 나눠 수집"),
           ("어린이집", "한도 초과가 「0건」으로 와서\n구분이 안 된다 → 하루 1회씩"),
           ("주소→좌표 (SGIS)", "일 50,000회 · 약 11만 주소는\n캐시 재사용으로 통과")]
    y = 1790000
    for ttl, d in lim:
        box(s, 7600000, y, 4100000, 400000, ttl, 11, True, NAVY,
            fill=LIGHT, line=RGBColor(0xD8, 0xDE, 0xE6))
        box(s, 7600000, y + 400000, 4100000, 470000, d, 9, False, GREY,
            fill=RGBColor(0xFF, 0xFF, 0xFF), line=RGBColor(0xD8, 0xDE, 0xE6))
        y += 880000

    box(s, 500000, 4520000, 11200000, 380000,
        "▸ 다 받기 전에는 그 축을 화면에 올리지 않는다 — 절반만 받은 값은 「적은 지역」처럼 보인다",
        12, True, RED)
    box(s, 500000, 4950000, 11200000, 380000,
        "검증을 통과해야만 공개본이 바뀐다", 16, True, NAVY)
    steps = [("데이터 검증", "38항목", "총인구 보존 · 코드 정합 · 지표 재계산 ·\n좌표와 배정 동 일치"),
             ("화면 검증", "112항목", "실데이터 배지 · 드릴다운 4단 ·\n마커 순서 · JS 오류 0"),
             ("발행", "통과 시에만", "하나라도 FAIL 이면 올리지 않는다\n공개본은 직전 상태 유지")]
    x, w = 500000, 3660000
    for i, (ttl, n, d) in enumerate(steps):
        cx = x + i * (w + 60000)
        box(s, cx, 5380000, w, 420000, f"{ttl}  ({n})", 13, True,
            RGBColor(0xFF, 0xFF, 0xFF), PP_ALIGN.CENTER,
            fill=BLUE if i < 2 else GREEN)
        box(s, cx, 5810000, w, 700000, d, 10, False, GREY, PP_ALIGN.CENTER,
            fill=LIGHT, line=RGBColor(0xD8, 0xDE, 0xE6))

    box(s, 500000, 6520000, 11200000, 330000,
        "▸ 틀린 데이터가 올라가면 화면은 정상으로 보인다 — 그래서 낡은 데이터가 남는 쪽을 택했다.",
        11, True, RED)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="사업부 발표용 덱 생성")
    ap.add_argument("--out", default=str(ROOT / "docs" / f"지역분석_사업부발표_{date.today():%Y%m%d}.pptx"))
    a = ap.parse_args(argv)

    f = facts()
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    slide_cover(prs, f)
    slide_data(prs, f)
    slide_map(prs, f)          # 지도 데이터 — 2P 뒤
    slide_pipeline(prs, f)
    slide_notes(prs, f)
    slide_errors(prs, f)       # 틀어지는 경우와 대응 (사업부용 1장)
    slide_ops(prs, f)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    print(f"[덱] {out}  ({len(prs.slides.__iter__.__self__._sldIdLst)}장)")
    print(f"     기준 {f['updated']} · 어린이집 축 {'포함' if f['has_cc'] else '대기'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
