# -*- coding: utf-8 -*-
"""
build_kapt.py — K-apt 단지 세대수를 행정동에 배정하고 집계  〔2026-08-03 신설, A-16〕

  입력  data/raw/kapt/basis_<ym>.json      collect_kapt.py 수집분
  출력  data/processed/kapt.json           행정동/시군구/시도 3레벨 세대수 집계

행정동 배정은 **도로명주소 지오코딩 + 좌표 PIP** 로 한다(C-6 재사용).
K-apt 응답의 `bjdCode` 는 **법정동**이라 행정동과 N:M 이다 — 조인에 쓰지 않는다
(DATA_CONTRACT §2-2 · 프로젝트 전반의 원칙).

사용법
  python scripts/build_kapt.py                # 수집 완료분으로 집계
  python scripts/build_kapt.py --no-geocode   # 캐시에 있는 주소만 (오프라인)
  python scripts/build_kapt.py --allow-partial  # 수집 미완인 채로 산출 (partial 표시)

⚠️ **수집이 끝나기 전에 집계하면 세대수가 적게 나온다.**
   적게 나오는 오류는 화면에서 "아파트가 적은 동"으로 보이고 눈에 안 띈다.
   그래서 진행률이 98% 미만이면 **기본적으로 중단**하고, `--allow-partial` 로만
   진행하되 `meta.partial=true` 와 진행률을 반드시 남긴다(대시보드가 그걸 표시한다).

⚠️ **세대수 0 인 단지가 실제로 있다**(소규모·오피스텔형, 실측 약 5%).
   0 을 그대로 더하면 "값이 있는데 0"인지 "자료가 없어서 0"인지 구분이 사라진다.
   → 0 은 **결측으로 빼고 세고**, 그 건수를 meta 에 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "kapt"
PROC = ROOT / "data" / "processed"
OUT = PROC / "kapt.json"

MIN_DONE = 0.98          # 이 밑이면 집계하지 않는다


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def num(v) -> int | None:
    try:
        n = int(float(v))
        return n if n > 0 else None      # 0 = 결측 취급 (위 ⚠️ 참조)
    except (TypeError, ValueError):
        return None


# 🆕 2026-08-04 — bjdCode 동종배치 추론에 필요한 최소 확증 수.
# 같은 법정동의 다른 단지가 **2곳 이상** 모두 같은 행정동에 떨어졌을 때만
# "이 법정동은 사실상 1:1" 이라고 본다. 1곳만 보고 따라가면 그 1곳이 틀렸을 때
# 같이 틀리고, 법정동이 여러 행정동으로 쪼개진 지역에서 조용히 오배정된다.
BJD_MIN_CONFIRM = 2

# 🆕 2026-08-05 (C-5) — 소비력 보정축 재료
#   `codeSaleNm` 은 '분양 / 임대 / 혼합 / 사택 및 관사 등' 4종이다(실측).
#   ⚠️ **'혼합'을 임대에 더하지 말 것.** 혼합은 한 단지 안에 분양·임대가 섞인 것이고
#      그 비율은 API 에 없다 — 임대로 세면 임대비중이 과대, 버리면 과소가 된다.
#      그래서 `mixed_hh` 로 **따로** 남겨 화면·문서에서 폭으로 읽을 수 있게 한다.
#   `kaptUsedate` 는 준공일 8자리(YYYYMMDD). 빈 문자열·None 이 실측 56건 있다.
RENTAL_SALE_NM = "임대"
MIXED_SALE_NM = "혼합"
NEW_YEARS = 10          # 준공 N년 이내 = '신축' (계약 §2-4)


def use_year(v) -> int | None:
    """kaptUsedate(YYYYMMDD) → 준공 연도. 값이 이상하면 None."""
    s = str(v or "").strip()
    if len(s) < 4 or not s[:4].isdigit():
        return None
    y = int(s[:4])
    return y if 1900 <= y <= date.today().year + 1 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="K-apt 세대수 집계")
    ap.add_argument("--ym", default=date.today().strftime("%Y%m"))
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--allow-partial", action="store_true")
    a = ap.parse_args(argv)

    basis = read(RAW / f"basis_{a.ym}.json")
    lst = read(RAW / f"list_{a.ym}.json")
    if not basis or not lst:
        raise SystemExit("[FATAL] collect_kapt.py 를 먼저 실행할 것")

    total = sum(len(v) for v in lst.values())
    done = len(basis) / max(total, 1)
    print(f"[kapt] 수집 {len(basis):,}/{total:,} ({done*100:.1f}%)")
    if done < MIN_DONE and not a.allow_partial:
        raise SystemExit(
            f"[중단] 수집이 {done*100:.1f}% 라 집계하면 세대수가 적게 나온다.\n"
            f"       collect_kapt.py 를 마저 돌리거나, 의도한 것이면 --allow-partial")

    # ---- 주소 → 행정동 : 3단계 폴백 (2026-08-04 신설) ----
    #   ① 도로명주소(doroJuso)          ← 본선
    #   ② 지번주소(kaptAddr)            ← 도로명이 아예 없는 단지가 실측 133건 있다
    #   ③ bjdCode 동종배치 추론          ← 같은 법정동의 다른 단지들이 전부 한 행정동에
    #                                     떨어졌을 때만. 1:N 법정동은 건드리지 않는다
    #
    # ⚠️ ③ 을 "법정동코드를 행정동코드로 변환"으로 오해하지 말 것. 그건 이 프로젝트가
    #    금지한 bjd2adm(53%) 이다. 여기서는 변환표를 쓰지 않고 **이번 데이터에서 관측된
    #    배치**만 근거로 삼으며, 관측이 갈리면(2개 이상 행정동) 배정을 포기한다.
    recs = list(basis.values())
    addrs = [r.get("doroJuso") or "" for r in recs]
    no_addr = sum(1 for x in addrs if not x)
    jibun = [(r.get("kaptAddr") or "") for r in recs]

    if a.no_geocode:
        cache = (read(PROC / "geocode_cache.json") or {}).get("data") or {}
        hits = {ad: cache.get(ad) for ad in set(addrs) if ad}
        hits.update({ad: cache.get(ad) for ad in set(jibun) if ad and ad not in hits})
    else:
        from geocode_addr import Geocoder            # noqa: E402
        g = Geocoder(workers=8, verbose=True)
        uniq = sorted({x for x in addrs if x})
        print(f"[kapt] ① 도로명주소 지오코딩 {len(uniq):,}건 (유니크)")
        g.batch(uniq, progress_every=2000)
        hits = {ad: g.one(ad) for ad in uniq}

        # ② 도로명으로 못 잡은 단지만 지번주소로 재시도
        retry = sorted({jb for ad, jb in zip(addrs, jibun)
                        if jb and not (ad and hits.get(ad))})
        if retry:
            print(f"[kapt] ② 지번주소로 재시도 {len(retry):,}건")
            g.batch(retry, progress_every=500)
            for jb in retry:
                got = g.one(jb)
                if got:
                    hits[jb] = got
        g.save()

    def locate(i: int):
        """단계별로 행정동을 찾는다. 반환 (adm_cd, 단계) — 못 찾으면 (None, None)."""
        ad, jb = addrs[i], jibun[i]
        if ad and hits.get(ad):
            return hits[ad]["adm_cd"], "doro"
        if jb and hits.get(jb):
            return hits[jb]["adm_cd"], "jibun"
        return None, None

    # ③ bjdCode 동종배치 추론표 — ①② 로 확정된 단지들만 근거로 만든다
    obs: dict[str, set[str]] = defaultdict(set)
    obs_n: dict[str, int] = defaultdict(int)
    for i, r in enumerate(recs):
        cd, _ = locate(i)
        bjd = (r.get("bjdCode") or "").strip()
        if cd and bjd:
            obs[bjd].add(cd)
            obs_n[bjd] += 1
    bjd_map = {b: next(iter(s)) for b, s in obs.items()
               if len(s) == 1 and obs_n[b] >= BJD_MIN_CONFIRM}
    split_bjd = sum(1 for s in obs.values() if len(s) > 1)

    agg = defaultdict(lambda: {"households": 0, "complexes": 0,
                               "hh_known": 0, "hh_zero": 0,
                               # 🆕 2026-08-05 (C-5) — 비율은 여기서 계산하지 않고
                               # **분자·분모를 그대로** 남긴다. 상위 레벨 합산이 단순
                               # 덧셈으로 끝나고(비율을 평균 내면 큰 동이 작은 동에
                               # 묻힌다), 나중에 정의가 바뀌어도 재계산이 된다.
                               "rental_hh": 0, "mixed_hh": 0,
                               "new_hh": 0, "age_hh": 0, "age_wsum": 0})
    placed = failed = 0
    zero_hh = 0
    by_step = defaultdict(int)
    ho_used = ho_sum = 0
    cur_y = date.today().year
    no_usedate = 0
    for i, r in enumerate(recs):
        cd, step = locate(i)
        if not cd:                                   # ③ 마지막 폴백
            bjd = (r.get("bjdCode") or "").strip()
            cd = bjd_map.get(bjd)
            step = "bjd" if cd else None
        if not cd:
            failed += 1
            continue
        by_step[step] += 1

        # 세대수 — kaptdaCnt 가 0/결측이면 hoCnt(호수)로 대체한다 (2026-08-04).
        # 주상복합·오피스텔형은 세대수 대신 호수로 신고하는 경우가 많고, 이들이
        # **도심에 몰려 있어** 버리면 강남·마포 같은 곳이 실제보다 적게 나온다.
        # ⚠️ 호수 ≠ 세대수다(같은 건물의 상가 호실이 섞일 수 있다). 그래서 대체분은
        #    건수·합계를 meta 에 따로 남겨 어디까지가 대체값인지 되짚을 수 있게 한다.
        hh = num(r.get("kaptdaCnt"))
        if hh is None:
            ho = num(r.get("hoCnt"))
            if ho is not None:
                hh = ho
                ho_used += 1
                ho_sum += ho

        d = agg[cd]
        d["complexes"] += 1
        if hh is None:
            d["hh_zero"] += 1
            zero_hh += 1
        else:
            d["households"] += hh
            d["hh_known"] += 1

            # 🆕 2026-08-05 (C-5) — 세대수를 아는 단지만 보정축 분자·분모에 넣는다.
            # 세대수가 결측인 단지를 분모에 넣으면 비율이 조용히 낮아진다.
            sale = (r.get("codeSaleNm") or "").strip()
            if sale == RENTAL_SALE_NM:
                d["rental_hh"] += hh
            elif sale == MIXED_SALE_NM:
                d["mixed_hh"] += hh

            y = use_year(r.get("kaptUsedate"))
            if y is None:
                no_usedate += 1
            else:
                d["age_hh"] += hh
                d["age_wsum"] += hh * (cur_y - y)
                if cur_y - y <= NEW_YEARS:
                    d["new_hh"] += hh
        placed += 1

    print(f"[kapt] 행정동 배정 {placed:,} · 실패 {failed:,} "
          f"(주소없음 {no_addr:,} 포함) · 행정동 {len(agg):,}개")
    print(f"[kapt]   └ 경로별 — 도로명 {by_step['doro']:,} · 지번 {by_step['jibun']:,} · "
          f"bjd추론 {by_step['bjd']:,} (1:1 법정동 {len(bjd_map):,}개 · 쪼개진 법정동 {split_bjd:,}개는 미사용)")
    if ho_used:
        print(f"[kapt]   └ 세대수 0 → hoCnt 대체 {ho_used:,}건 · {ho_sum:,}호 회수")
    if no_usedate:
        print(f"[kapt]   └ 준공일 없음 {no_usedate:,}건 — 준공연차·신축비중 분모에서 제외")
    print(f"[kapt] ⚠️ 세대수 0/결측 단지 {zero_hh:,}건 — 합계에서 제외했다")

    # ---- 상위 레벨 ----
    def roll(cut: int):
        out: dict = defaultdict(dict)
        for k, v in agg.items():
            t = out[k[:cut]]
            for f, n in v.items():
                t[f] = t.get(f, 0) + n
        return dict(out)

    sgg, sido = roll(5), roll(2)
    tot_hh = sum(v["households"] for v in agg.values())
    print(f"[kapt] 세대수 합계 {tot_hh:,}세대 · 시군구 {len(sgg)} · 시도 {len(sido)}")

    # 경계에 없는 행정동 (조인 실패 = 화면에서 증발)
    met = read(PROC / "metrics.json")
    known = set((met or {}).get("data") or {})
    orphan = [k for k in agg if known and k not in known]
    if orphan:
        print(f"[kapt] ⚠️ metrics 에 없는 행정동 {len(orphan)}건: {orphan[:5]}")

    meta = {
        "sample": False,
        "updated": date.today().isoformat(),
        "base_ym": a.ym,
        "source": "공공데이터포털 K-apt (AptListService4 + AptBasisInfoServiceV5)",
        "collected": len(basis),
        "total_complexes": total,
        "progress": round(done, 4),
        "partial": done < MIN_DONE,          # ⚠️ 대시보드가 이 값을 보고 경고한다
        "placed": placed,
        "geocode_failed": failed,
        "no_doro_addr": no_addr,
        "zero_households": zero_hh,
        "households_total": tot_hh,
        # 🆕 2026-08-04 — 어디까지가 폴백으로 건진 값인지 되짚을 수 있게 남긴다
        "placed_by": {"doro": by_step["doro"], "jibun": by_step["jibun"],
                      "bjd": by_step["bjd"]},
        "bjd_map_size": len(bjd_map),
        "bjd_split_skipped": split_bjd,
        "ho_fallback": {"complexes": ho_used, "units": ho_sum},
        # 🆕 2026-08-05 (C-5) 소비력 보정축 재료
        "no_usedate": no_usedate,
        "new_years": NEW_YEARS,
        "rental_hh_total": sum(v["rental_hh"] for v in agg.values()),
        "mixed_hh_total": sum(v["mixed_hh"] for v in agg.values()),
        "new_hh_total": sum(v["new_hh"] for v in agg.values()),
        "age_hh_total": sum(v["age_hh"] for v in agg.values()),
        "note": ("세대수는 kaptdaCnt 우선, 0/결측이면 hoCnt(호수)로 대체한다"
                 "(대체분은 ho_fallback 참조). 그래도 없으면 결측으로 보고 합계에서 뺀다. "
                 "행정동은 ①도로명 ②지번 지오코딩 + 좌표 PIP, ③같은 법정동의 다른 단지가 "
                 "2곳 이상 모두 한 행정동에 떨어진 경우에만 그 값을 따른다. "
                 "bjdCode→행정동 변환표는 쓰지 않는다(법정동:행정동 N:M). "
                 "rental_hh/mixed_hh/new_hh/age_hh/age_wsum 은 비율이 아니라 "
                 "분자·분모 원값이다(상위 레벨은 단순 합산). '혼합' 단지는 분양·임대 "
                 "구성비가 API 에 없어 임대에 더하지 않고 mixed_hh 로 분리했다 — "
                 "임대비중의 하한이 rental, 상한이 rental+mixed 다."),
    }
    PROC.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"meta": meta, "data": dict(agg),
                               "sgg": sgg, "sido": sido},
                              ensure_ascii=False), encoding="utf-8")
    print(f"[kapt] → {OUT.name}")
    if meta["partial"]:
        print("⚠️ partial=true — 수집이 끝나면 다시 돌릴 것 (지금 값은 과소계상이다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
