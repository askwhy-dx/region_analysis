# -*- coding: utf-8 -*-
"""
verify_population.py — 인구 데이터 교차 검증 (독립 원천 대조)

`population.json` 은 jumin.mois.go.kr CSV 를 파싱해 만든다(collect_population.py).
그 값이 맞는지를 **다른 배포 경로**로 받은 같은 원천 데이터와 대조한다.

  검증 원천: data.go.kr 15097972
             행정안전부_지역별(행정동) 성별 연령별 주민등록 인구수
             https://www.data.go.kr/data/15097972/fileData.do
             ⭐ 인증키·로그인 불필요 (2026-07-30 확인)

  왜 필요한가
    인구는 이 프로젝트에서 유일한 실데이터 지표이고, 학령인구는 점수 산식의
    size_pop(0.25) 과 comp_academy(0.30) 분자에 동시에 들어간다.
    여기가 틀리면 전 지역 점수가 조용히 틀린다. 상담 자료라 방어가 불가능해진다.

  두 원천의 표현 차이 (숫자는 같다 — 2026-07-30 전수 확인, 불일치 0건)
    ① 행정구역 코드: data.go.kr 은 기준일 시점 코드라 개편 전 구코드를 쓴다.
       예) 2026-06-30 기준 파일은 광주 29 / 전남 46 (2026-07-01 통합 이전).
       → code_legacy_map.json 으로 신코드 변환 후 비교한다.
    ② 출장소: data.go.kr 은 출장소를 독립 행으로 분리, jumin 은 본동에 합산.
       예) 화도읍 63,405 + 동부출장소 12,932 + 남부출장소 35,150 = 111,487
       → code_legacy_map.json 의 branch_office 매핑으로 본동에 합산 후 비교한다.

  ⚠️ atchFileId 는 월 갱신마다 바뀐다. 하드코딩하지 않고 데이터셋 페이지의
     JSON-LD `contentUrl` 에서 매번 추출한다.

사용법
  python scripts/verify_population.py                 # 다운로드 + 검증
  python scripts/verify_population.py --no-download   # 캐시된 원본으로만 검증
  python scripts/verify_population.py --strict        # 불일치 시 RuntimeError
  python scripts/verify_population.py --report out.json

종료 코드: 0 = 일치 / 1 = 불일치 / 2 = 검증 불가(원본·입력 없음)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
RAW_DIR = ROOT / "data" / "raw" / "population"

DATASET_PK = "15097972"
DATASET_PAGE = f"https://www.data.go.kr/data/{DATASET_PK}/fileData.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

BUCKETS = ["0_5", "6_11", "12_14", "15_17", "18_64", "65_"]
SCHOOL_AGE_MIN = 3
SCHOOL_AGE_MAX = 16


# ────────────────────────────── 유틸 ──────────────────────────────

def log(msg: str) -> None:
    print(msg, flush=True)


def read_json(p: Path):
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def to_int(s) -> int:
    s = str(s or "").replace(",", "").strip()
    return int(s) if s.lstrip("-").isdigit() else 0


def bucket_of(age: int) -> str:
    if age <= 5:
        return "0_5"
    if age <= 11:
        return "6_11"
    if age <= 14:
        return "12_14"
    if age <= 17:
        return "15_17"
    if age <= 64:
        return "18_64"
    return "65_"


# ────────────────────────────── 원본 확보 ──────────────────────────────

def resolve_download_url(session) -> str:
    """데이터셋 페이지 JSON-LD 의 contentUrl 을 뽑는다 (atchFileId 는 월마다 바뀜)."""
    r = session.get(DATASET_PAGE, timeout=60)
    r.raise_for_status()
    urls = re.findall(r'"contentUrl"\s*:\s*"([^"]+fileDownload\.do[^"]+)"', r.text)
    if not urls:
        raise RuntimeError(
            "다운로드 URL(contentUrl)을 찾지 못했습니다. 포털 페이지 구조가 바뀐 것 같습니다. "
            f"수동 확인: {DATASET_PAGE}")
    return urls[0].replace("&amp;", "&")


def download_verify_csv(raw_dir: Path) -> Path:
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests 미설치 — --no-download 로 캐시 파일을 쓰세요")
    s = requests.Session()
    s.headers["User-Agent"] = UA
    url = resolve_download_url(s)
    log(f"  다운로드 URL: {url}")
    r = s.get(url, timeout=600, stream=True, headers={"Referer": DATASET_PAGE})
    r.raise_for_status()
    ct = (r.headers.get("Content-Type") or "").lower()
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / "verify_dgk_age.csv"
    n = 0
    with open(p, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
            n += len(chunk)
    if b"<html" in p.read_bytes()[:2000].lower():
        raise RuntimeError(f"HTML 응답 (다운로드 실패). Content-Type={ct}")
    log(f"  받음: {p.name}  {n/1024/1024:.1f} MB")
    return p


def decode_csv(raw: bytes) -> str:
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")


# ────────────────────────────── 검증 원천 집계 ──────────────────────────────

def build_conv() -> dict:
    """code_legacy_map.json → 구코드/출장소 → 현행 행정동코드(8) 변환표."""
    leg = read_json(PROCESSED / "code_legacy_map.json")
    if not leg:
        raise RuntimeError("code_legacy_map.json 없음 — geo_legacy_map.py 를 먼저 실행하세요")
    conv: dict[str, str] = {}
    for key, val in leg.items():
        if not isinstance(val, dict):
            continue
        for a, b in val.items():
            if (isinstance(a, str) and isinstance(b, str)
                    and a.isdigit() and b.isdigit()):
                conv[a] = b
    # collect_population 과 같은 규칙을 쓴다 (SSOT 는 그쪽) — 경계 지연 코드 되돌림 (2026-10-12 관리자 승인)
    from collect_population import BOUNDARY_LAG
    conv.update(BOUNDARY_LAG)
    log(f"  code_legacy_map: 변환 엔트리 {len(conv)}개 "
        f"(adm {len(leg.get('adm') or {})} / branch_office {len(leg.get('branch_office') or {})})")
    return conv


def aggregate_source(csv_path: Path, conv: dict) -> tuple[dict, dict]:
    rows = list(csv.reader(io.StringIO(decode_csv(csv_path.read_bytes()))))
    if len(rows) < 2:
        raise RuntimeError(f"{csv_path.name} 이 비었습니다")
    hdr, body = rows[0], rows[1:]

    # 연령 컬럼: '0세남자' ~ '109세여자' + '110세이상 남자'(공백 주의)
    age_idx: dict[int, int] = {}
    for i, h in enumerate(hdr):
        m = re.match(r"^(\d+)세(남자|여자)$", (h or "").strip())
        if m:
            age_idx[i] = int(m.group(1))
        elif "이상" in (h or "") and "세" in h:
            mm = re.search(r"(\d+)\s*세", h)
            if mm:
                age_idx[i] = int(mm.group(1))
    if len(age_idx) < 200:
        raise RuntimeError(f"연령 컬럼 인식 실패({len(age_idx)}개) — 파일 레이아웃이 바뀐 것 같습니다")

    try:
        ym_col = hdr.index("기준연월")
    except ValueError:
        ym_col = 1

    agg: dict[str, dict] = {}
    office_keys: set[str] = set()
    base_ym = set()
    stat = {"rows": 0, "converted": 0, "branch_rows": 0, "skipped": 0}
    for r in body:
        code10 = (r[0] or "").strip()
        if len(code10) != 10 or not code10.isdigit():
            stat["skipped"] += 1
            continue
        stat["rows"] += 1
        if len(r) > ym_col:
            ym = re.sub(r"\D", "", str(r[ym_col]))[:6]
            if ym:
                base_ym.add(ym)
        src8 = code10[:8]
        if "출장소" in (r[4] if len(r) > 4 else ""):
            stat["branch_rows"] += 1
            if src8 not in conv:
                office_keys.add(src8)
        code = conv.get(src8, src8)
        if code != src8:
            stat["converted"] += 1
        rec = agg.setdefault(code, {"total": 0, "age": dict.fromkeys(BUCKETS, 0),
                                    "age_year": {str(a): 0 for a in range(18)}})
        rec["total"] += to_int(r[5] if len(r) > 5 else 0)
        for i, a in age_idx.items():
            if i < len(r):
                value = to_int(r[i])
                rec["age"][bucket_of(a)] += value
                if a <= 17:
                    rec["age_year"][str(a)] += value

    # 변환표에 없는 출장소 → 같은 시군구 바로 아래 코드에 합산 (collect_population.convert_legacy 와 같은 규칙)
    from collect_population import D1_BRANCH_OFFICES
    for k in sorted(office_keys - D1_BRANCH_OFFICES):
        parents = [c for c in agg if c[:5] == k[:5] and c < k and c not in office_keys]
        if parents and k in agg:
            src, dst = agg.pop(k), agg[max(parents)]
            dst["total"] += src["total"]
            for b in BUCKETS:
                dst["age"][b] += src["age"][b]
            for a in dst["age_year"]:
                dst["age_year"][a] += src["age_year"][a]
            stat["converted"] += 1

    stat["age_cols"] = len(age_idx)
    stat["base_ym"] = sorted(base_ym)
    stat["keys"] = len(agg)
    return agg, stat


# ────────────────────────────── 대조 ──────────────────────────────

def rollup(data: dict, getter, keylen: int) -> dict:
    out: dict[str, dict] = {}
    for k, v in data.items():
        o = out.setdefault(k[:keylen], {"total": 0, "age": dict.fromkeys(BUCKETS, 0)})
        o["total"] += getter(v, "total")
        for b in BUCKETS:
            o["age"][b] += getter(v, b)
    return out


def ours_get(v, field):
    if field == "total":
        return v.get("total") or 0
    return (v.get("age") or {}).get(field) or 0


def src_get(v, field):
    return v["total"] if field == "total" else v["age"][field]


def compare(ours: dict, src: dict, level: str, keylen: int) -> dict:
    a = ours if keylen == 8 else rollup(ours, ours_get, keylen)
    b = src if keylen == 8 else rollup(src, src_get, keylen)
    ga = ours_get if keylen == 8 else src_get      # rollup 결과는 src 형태
    gb = src_get

    keys = set(a) | set(b)
    res = {"level": level, "keys": len(keys),
           "only_ours": sorted(set(a) - set(b))[:20],
           "only_src": sorted(set(b) - set(a))[:20],
           "only_ours_n": len(set(a) - set(b)),
           "only_src_n": len(set(b) - set(a)),
           "total_mismatch": [], "bucket_mismatch": {}, "school_age_mismatch": []}

    for k in sorted(keys):
        va, vb = a.get(k), b.get(k)
        ta = ga(va, "total") if va else 0
        tb = gb(vb, "total") if vb else 0
        if ta != tb:
            res["total_mismatch"].append({"code": k, "ours": ta, "src": tb, "diff": ta - tb})

    for bk in BUCKETS:
        bad = []
        for k in sorted(keys):
            va, vb = a.get(k), b.get(k)
            xa = ga(va, bk) if va else 0
            xb = gb(vb, bk) if vb else 0
            if xa != xb:
                bad.append({"code": k, "ours": xa, "src": xb, "diff": xa - xb})
        res["bucket_mismatch"][bk] = bad

    if keylen == 8:
        for k in sorted(keys):
            o = ours.get(k) or {}
            s = src.get(k) or {"age": dict.fromkeys(BUCKETS, 0), "age_year": {}}
            osa = o.get("school_age") or 0
            ssa = sum((s.get("age_year") or {}).get(str(a), 0)
                      for a in range(SCHOOL_AGE_MIN, SCHOOL_AGE_MAX + 1))
            if osa != ssa:
                res["school_age_mismatch"].append(
                    {"code": k, "ours": osa, "src": ssa, "diff": osa - ssa})

    res["ok"] = (not res["total_mismatch"]
                 and not any(res["bucket_mismatch"].values())
                 and not res["school_age_mismatch"]
                 and res["only_ours_n"] == 0 and res["only_src_n"] == 0)
    return res


def print_result(r: dict, names: dict) -> None:
    mark = "✅" if r["ok"] else "❌"
    log(f"\n{mark} [{r['level']}] 비교 대상 {r['keys']}개")
    if r["only_ours_n"] or r["only_src_n"]:
        log(f"   키 차이: 우리만 {r['only_ours_n']}건 {r['only_ours'][:5]} / "
            f"검증원천만 {r['only_src_n']}건 {r['only_src'][:5]}")
    log(f"   총인구 불일치 {len(r['total_mismatch'])}건")
    for d in sorted(r["total_mismatch"], key=lambda x: -abs(x["diff"]))[:8]:
        log(f"     {d['code']} {names.get(d['code']) or ''}: "
            f"우리 {d['ours']:,} vs 원천 {d['src']:,} ({d['diff']:+,})")
    parts = " ".join(f"{b}={len(v)}" for b, v in r["bucket_mismatch"].items())
    log(f"   연령구간 불일치: {parts}")
    for b, v in r["bucket_mismatch"].items():
        for d in sorted(v, key=lambda x: -abs(x["diff"]))[:3]:
            log(f"     [{b}] {d['code']} {names.get(d['code']) or ''}: "
                f"우리 {d['ours']:,} vs 원천 {d['src']:,} ({d['diff']:+,})")
    if r["level"] == "행정동":
        log(f"   학령인구(3~16) 불일치 {len(r['school_age_mismatch'])}건")
        for d in sorted(r["school_age_mismatch"], key=lambda x: -abs(x["diff"]))[:5]:
            log(f"     {d['code']} {names.get(d['code']) or ''}: "
                f"우리 {d['ours']:,} vs 원천 {d['src']:,} ({d['diff']:+,})")


# ────────────────────────────── main ──────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="인구 데이터 교차 검증 (data.go.kr 독립 원천)")
    ap.add_argument("--no-download", action="store_true",
                    help="네트워크 접근 없이 캐시된 verify_dgk_age.csv 만 사용")
    ap.add_argument("--strict", action="store_true",
                    help="불일치 시 RuntimeError (배치 파이프라인용)")
    ap.add_argument("--report", default="", help="결과를 JSON 으로 저장할 경로")
    ap.add_argument("--raw", default=str(RAW_DIR), help="원본 캐시 디렉터리")
    a = ap.parse_args(argv)

    log("=" * 72)
    log("인구 교차 검증 — jumin.mois.go.kr(정본) vs data.go.kr 15097972(검증)")
    log("=" * 72)

    ours_doc = read_json(PROCESSED / "population.json")
    if not ours_doc or not ours_doc.get("data"):
        log("! population.json 없음 → collect_population.py 를 먼저 실행하세요")
        return 2
    ours = ours_doc["data"]
    ours_meta = ours_doc.get("meta") or {}
    log(f"\n검증 대상: population.json  {len(ours):,}동  기준 {ours_meta.get('base_ym')}  "
        f"총 {sum((v.get('total') or 0) for v in ours.values()):,}명")

    raw_dir = Path(a.raw)
    csv_path = raw_dir / "verify_dgk_age.csv"
    if a.no_download:
        if not csv_path.exists():
            log(f"! {csv_path} 없음 — --no-download 를 빼고 실행하세요")
            return 2
        log(f"\n[원본] 캐시 사용: {csv_path.name}")
    else:
        log("\n[원본] data.go.kr 다운로드")
        try:
            csv_path = download_verify_csv(raw_dir)
        except Exception as e:
            log(f"! 다운로드 실패: {e}")
            if csv_path.exists():
                log(f"  → 캐시 {csv_path.name} 로 대체 진행")
            else:
                return 2

    log("\n[집계] 구코드 변환 + 출장소 합산")
    conv = build_conv()
    src, stat = aggregate_source(csv_path, conv)
    log(f"  데이터행 {stat['rows']:,} · 연령컬럼 {stat['age_cols']} · "
        f"출장소행 {stat['branch_rows']} · 코드변환 {stat['converted']:,} "
        f"→ 집계키 {stat['keys']:,}")
    log(f"  원천 기준연월: {stat['base_ym']}  총 {sum(v['total'] for v in src.values()):,}명")

    if stat["base_ym"] and ours_meta.get("base_ym") not in stat["base_ym"]:
        log(f"\n⚠️ 기준연월 불일치 — 우리 {ours_meta.get('base_ym')} vs 원천 {stat['base_ym']}")
        log("   서로 다른 달을 비교하면 불일치는 당연하다. 같은 달로 맞춘 뒤 다시 검증할 것.")

    cm = read_json(PROCESSED / "code_map.json") or {}
    names = {}
    for lv in ("sido", "sgg", "adm"):
        for x in (cm.get(lv) or []):
            if isinstance(x, dict) and x.get("code"):
                names[x["code"]] = x.get("name")

    results = [
        compare(ours, src, "전국", 2),
        compare(ours, src, "시군구", 5),
        compare(ours, src, "행정동", 8),
    ]
    for r in results:
        print_result(r, names)

    all_ok = all(r["ok"] for r in results)
    log("\n" + "=" * 72)
    log("판정: " + ("✅ 두 원천 완전 일치 — population.json 신뢰 가능"
                  if all_ok else "❌ 불일치 발견 — 아래 조치 필요"))
    if not all_ok:
        log("  ① 기준연월이 같은지 확인 (다른 달이면 당연히 다르다)")
        log("  ② code_legacy_map.json 이 최신 행정구역 개편을 반영했는지 확인")
        log("     (개칭은 MANUAL_ADM 수기 등재만 가능 — geo_legacy_map.py 참조)")
        log("  ③ 출장소 신설·폐지가 있었는지 확인 (branch_office 매핑)")
        log("  ④ 위 셋이 아니면 collect_population.py 파싱을 의심할 것")
    log("=" * 72)

    if a.report:
        out = {
            "verified_at": date.today().isoformat(),
            "ok": all_ok,
            "ours": {"file": "population.json", "count": len(ours),
                     "base_ym": ours_meta.get("base_ym"),
                     "total": sum((v.get("total") or 0) for v in ours.values())},
            "source": {"dataset": DATASET_PK, "page": DATASET_PAGE,
                       "base_ym": stat["base_ym"], "keys": stat["keys"],
                       "total": sum(v["total"] for v in src.values()),
                       "branch_rows": stat["branch_rows"],
                       "converted": stat["converted"]},
            "levels": [{k: v for k, v in r.items() if k != "bucket_mismatch"}
                       | {"bucket_mismatch_counts":
                          {b: len(v) for b, v in r["bucket_mismatch"].items()}}
                       for r in results],
        }
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        log(f"리포트 저장: {a.report}")

    if not all_ok and a.strict:
        raise RuntimeError("인구 교차 검증 실패 — 위 로그 참조")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
