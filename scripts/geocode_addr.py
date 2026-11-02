# -*- coding: utf-8 -*-
"""
geocode_addr.py — 주소 → 행정동 변환 계층 (TODO: C-6)

SGIS 지오코딩으로 좌표를 얻고 **좌표 PIP 로 행정동을 판정**한다.
설계·함정·실측 근거는 docs/ADDR_TO_ADM.md 가 SSOT.

  경로B (정본)      주소 → SGIS geocode → x,y(EPSG:5179) → WGS84 → PIP → adm_cd(행안부8)
  경로A (교차검증)  응답 adm_cd(통계청8) → code_bridge → 행안부10 앞8

산출/캐시
  data/processed/geocode_cache.json
    {주소: {lng, lat, adm_cd, adm_nm, source, geo_ver, xcheck}}

사용법
  python scripts/geocode_addr.py --selftest              # 표본 6건 (ADDR_TO_ADM §3-1)
  python scripts/geocode_addr.py --addr "서울 강남구 테헤란로 152"
  python scripts/geocode_addr.py --file addrs.txt        # 한 줄 1주소 배치

라이브러리로 쓸 때
  from geocode_addr import Geocoder
  g = Geocoder()
  res = g.batch(["주소1", "주소2"])     # {주소: {...} | None}
  g.save()

⚠️ 함정 (docs/ADDR_TO_ADM.md §4)
  * 응답 `adm_cd` 는 **통계청 구코드**다(광주 24 / 전남 36). 단독 조인 금지 —
    전남광주통합 지역에서 브리지 조회가 실패하는 게 정상이다. 판정은 좌표가 한다.
  * `requests.Session` 재사용을 빠뜨리면 30배 느려진다 (1.4건/초 vs 88건/초).
  * **실패는 조용히 버리지 않는다.** 학원이 누락되면 comp_academy 가
    "경쟁 없는 좋은 동"으로 잘못 계산된다 — 상담 자리에서 가장 위험한 방향의 오류다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("[FATAL] requests 필요:  pip install requests", file=sys.stderr)
    raise

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admlookup import AdmIndex                      # noqa: E402
from collect_poi import CrsConverter                # noqa: E402  (EPSG:5179 → WGS84)

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
CACHE = PROC / "geocode_cache.json"
TOKEN = PROC / ".sgis_token.json"
BRIDGE = PROC / "code_bridge.json"

# 통계청 → 국가데이터처 개편으로 호스트가 바뀌는 중이라 순서대로 시도한다.
HOSTS = ["https://sgisapi.kostat.go.kr/OpenAPI3",
         "https://sgisapi.mods.go.kr/OpenAPI3"]

# 경계 버전 스탬프 — 경계가 개편되면 캐시된 판정을 재계산해야 한다 (E-3 선반영)
GEO_VER = "20260701"


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# --------------------------------------------------------------------------
# 주소 정제
# --------------------------------------------------------------------------

_POST = re.compile(r"^\s*\(?\d{5}\)?\s+")           # 앞에 붙은 우편번호
_PAREN = re.compile(r"\([^)]*\)")                   # (역삼동, OO빌딩)
_FLOOR = re.compile(r"\s+\d+층(\s*\d+호)?\s*$")      # 3층 201호
_HO = re.compile(r"\s+\d+호\s*$")


def clean_addr(a: str) -> str:
    """정제는 안전망이다 — 층·호·괄호가 붙어도 SGIS 는 대체로 통과한다(§3-2)."""
    a = (a or "").strip()
    a = _POST.sub("", a)
    a = _PAREN.sub(" ", a)
    a = _FLOOR.sub("", a)
    a = _HO.sub("", a)
    return re.sub(r"\s+", " ", a).strip()


# --------------------------------------------------------------------------

class Geocoder:
    def __init__(self, workers: int = 8, verbose: bool = True):
        env = load_env()
        self.key = env.get("SGIS_CONSUMER_KEY", "")
        self.secret = env.get("SGIS_CONSUMER_SECRET", "")
        if not self.key or not self.secret:
            raise RuntimeError("SGIS_CONSUMER_KEY/SECRET 이 .env 에 없다")
        self.workers = workers
        self.verbose = verbose
        self.s = requests.Session()                 # ⚠️ 재사용 필수 (§4-3)
        self.host = None
        self.token = None
        self.token_at = 0.0
        self._lock = threading.Lock()
        self.crs = CrsConverter("EPSG:5179")
        self.idx = AdmIndex.load(verbose=verbose)
        self.bridge = {}
        if BRIDGE.exists():
            self.bridge = json.loads(BRIDGE.read_text(encoding="utf-8")).get(
                "kostat8_to_mois10", {})
        self.cache = {}
        if CACHE.exists():
            try:
                c = json.loads(CACHE.read_text(encoding="utf-8"))
                # 경계 버전이 다르면 판정을 신뢰할 수 없다 → 버린다
                self.cache = {k: v for k, v in c.get("data", {}).items()
                              if v and v.get("geo_ver") == GEO_VER}
                if verbose and len(self.cache) != len(c.get("data", {})):
                    print(f"[geocode] 캐시 {len(c.get('data', {})):,}건 중 "
                          f"경계버전 {GEO_VER} 일치분 {len(self.cache):,}건만 사용")
            except Exception:
                self.cache = {}
        self.stat = {"cache_hit": 0, "ok": 0, "fail_geocode": 0,
                     "fail_pip": 0, "fallback_jibun": 0, "xcheck_mismatch": 0,
                     "xcheck_absent": 0}

    # ---- 인증 ----
    def _auth(self):
        with self._lock:
            if self.token and time.time() - self.token_at < 3000:
                return self.token
            last = None
            for h in ([self.host] if self.host else HOSTS):
                try:
                    r = self.s.get(f"{h}/auth/authentication.json",
                                   params={"consumer_key": self.key,
                                           "consumer_secret": self.secret},
                                   timeout=20)
                    j = r.json()
                    if str(j.get("errCd")) == "0":
                        self.host = h
                        self.token = j["result"]["accessToken"]
                        self.token_at = time.time()
                        if self.verbose:
                            print(f"[geocode] SGIS 인증 OK ({h})")
                        return self.token
                    last = j.get("errMsg")
                except Exception as e:
                    last = f"{type(e).__name__}: {e}"
            raise RuntimeError(f"SGIS 인증 실패: {last}")

    # ---- 1건 ----
    def _geocode_once(self, addr: str):
        tok = self._auth()
        try:
            r = self.s.get(f"{self.host}/addr/geocode.json",
                           params={"accessToken": tok, "address": addr,
                                   "pagenum": 0, "resultcount": 1},
                           timeout=20)
            j = r.json()
        except Exception:
            return None
        if str(j.get("errCd")) != "0":
            if "auth" in str(j.get("errMsg", "")).lower() or j.get("errCd") in (-401, "-401"):
                self.token = None
            return None
        rd = (j.get("result") or {}).get("resultdata") or []
        return rd[0] if rd else None

    def one(self, addr: str, jibun: str | None = None):
        """주소 → {lng, lat, adm_cd, ...} / 실패 시 None."""
        raw = (addr or "").strip()
        if not raw:
            return None
        if raw in self.cache:
            self.stat["cache_hit"] += 1
            return self.cache[raw]

        src = "road"
        d = self._geocode_once(clean_addr(raw))
        if not d and jibun:                       # 도로명 실패 → 지번 폴백 (§3-2)
            d = self._geocode_once(clean_addr(jibun))
            if d:
                src = "jibun"
                self.stat["fallback_jibun"] += 1
        if not d:
            self.stat["fail_geocode"] += 1
            self.cache[raw] = None
            return None

        try:
            lng, lat = self.crs.to_wgs84(float(d["x"]), float(d["y"]))
        except Exception:
            self.stat["fail_geocode"] += 1
            self.cache[raw] = None
            return None

        # 경로B — 좌표 PIP 가 정본
        adm = self.idx.lookup(lng, lat)
        if not adm:
            self.stat["fail_pip"] += 1
            self.cache[raw] = None
            return None

        # 경로A — 교차검증용. 전남광주통합에서 실패하는 것이 정상이다 (§4-1)
        xcheck = "absent"
        k8 = (d.get("adm_cd") or "").strip()
        m10 = self.bridge.get(k8)
        if m10:
            xcheck = "match" if m10[:8] == adm else "mismatch"
            if xcheck == "mismatch":
                self.stat["xcheck_mismatch"] += 1
        else:
            self.stat["xcheck_absent"] += 1

        out = {"lng": round(lng, 6), "lat": round(lat, 6),
               "adm_cd": adm, "adm_nm": (d.get("adm_nm") or "").strip(),
               "sgis_adm_cd": k8, "source": src, "geo_ver": GEO_VER,
               "xcheck": xcheck}
        self.cache[raw] = out
        self.stat["ok"] += 1
        return out

    # ---- 배치 ----
    def batch(self, addrs, jibuns=None, progress_every: int = 5000) -> dict:
        addrs = list(addrs)
        jibuns = list(jibuns) if jibuns else [None] * len(addrs)
        todo = [(a, j) for a, j in zip(addrs, jibuns) if a and a not in self.cache]
        if self.verbose:
            print(f"[geocode] 대상 {len(addrs):,}건 · 신규 {len(todo):,}건 "
                  f"· 캐시 {len(addrs) - len(todo):,}건 · {self.workers}스레드")
        self._auth()
        done = [0]

        def work(t):
            r = self.one(t[0], t[1])
            done[0] += 1
            if self.verbose and progress_every and done[0] % progress_every == 0:
                print(f"    ... {done[0]:,}/{len(todo):,}")
            return r

        if todo:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                list(ex.map(work, todo))
        return {a: self.cache.get(a) for a in addrs}

    def save(self):
        PROC.mkdir(parents=True, exist_ok=True)
        # ⚠️ meta 를 통째로 새로 쓰면 **다른 단계가 남긴 기록이 사라진다** (2026-08-12 실측).
        #    `arbitrate_adm.py` 가 남기는 arbiter·outlier_guard 가 지오코딩 한 번에 날아가서,
        #    "심판을 언제 몇 건 돌렸는지"가 조용히 없어졌다. 항목별 기록(entry 의 arbiter 등)은
        #    그대로였기에 값은 안 틀렸지만, 이력만 증발하는 종류의 사고다. → 병합해서 쓴다.
        keep = {}
        if CACHE.exists():
            try:
                keep = json.loads(CACHE.read_text(encoding="utf-8")).get("meta") or {}
            except Exception:  # noqa: BLE001
                keep = {}
        keep.update({"updated": date.today().isoformat(), "geo_ver": GEO_VER,
                     "source": "SGIS geocode + 좌표 PIP", "count": len(self.cache),
                     "stat": self.stat})
        CACHE.write_text(json.dumps({"meta": keep, "data": self.cache}, ensure_ascii=False),
                         encoding="utf-8")

    def report(self):
        s = self.stat
        tot = s["ok"] + s["fail_geocode"] + s["fail_pip"]
        print(f"\n[geocode] 성공 {s['ok']:,} / 시도 {tot:,} "
              f"({s['ok']/max(tot,1)*100:.1f}%) · 캐시적중 {s['cache_hit']:,}")
        print(f"[geocode] 실패 — 지오코딩 {s['fail_geocode']:,} · "
              f"경계밖 {s['fail_pip']:,} · 지번폴백 {s['fallback_jibun']:,}")
        print(f"[geocode] 교차검증 — 불일치 {s['xcheck_mismatch']:,} · "
              f"브리지없음 {s['xcheck_absent']:,} (전남광주통합은 없음이 정상)")
        if s["fail_geocode"] or s["fail_pip"]:
            print("⚠️ 실패분은 집계에서 빠진다. 학원이면 comp_academy 가 "
                  "'경쟁 없는 동'으로 과대평가되니 건수를 반드시 보고할 것")


# --------------------------------------------------------------------------

SELFTEST = [
    ("서울 강남구 테헤란로 152", "11680", "역삼1동"),
    ("서울 종로구 창신길 1", "11110", "창신"),
    ("제주 제주시 문연로 6", "50110", "연동"),
    ("광주 서구 내방로 111", "12240", "치평동"),
    ("전남 무안군 삼향읍 오룡길 1", "12810", "삼향"),
    ("서울 송파구 올림픽로 300", "11710", "잠실"),
]


def selftest() -> int:
    g = Geocoder(workers=4)
    ok = 0
    print("\n[selftest] ADDR_TO_ADM.md §3-1 표본 6건")
    for addr, sgg, nm in SELFTEST:
        r = g.one(addr)
        if r and r["adm_cd"].startswith(sgg):
            ok += 1
            print(f"  ✅ {addr}  → {r['adm_cd']} {r['adm_nm']} "
                  f"(xcheck={r['xcheck']})")
        else:
            print(f"  ❌ {addr}  → {r}")
    g.save()
    g.report()
    print(f"\n[selftest] {ok}/6 통과"
          f"{'  ← 전남광주통합 2건 포함' if ok == 6 else ''}")
    return 0 if ok == 6 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="주소 → 행정동 (C-6)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--addr", action="append", default=[])
    ap.add_argument("--file", help="한 줄에 주소 하나")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    addrs = list(a.addr)
    if a.file:
        addrs += [l.strip() for l in Path(a.file).read_text(encoding="utf-8").splitlines()
                  if l.strip()]
    if not addrs:
        ap.print_help()
        return 2

    g = Geocoder(workers=a.workers)
    res = g.batch(addrs)
    for k, v in res.items():
        print(f"{k}\t{v['adm_cd'] if v else 'FAIL'}\t{v['adm_nm'] if v else ''}")
    g.save()
    g.report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
