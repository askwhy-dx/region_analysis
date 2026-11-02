# -*- coding: utf-8 -*-
"""
adm_arbiter.py — 행정동 배정 분쟁의 심판 (E-4 공용 모듈)

**도로명주소의 행정동은 행안부가 정의한다.** 경계 폴리곤(admdongkor)도 통계청 분류도
그 파생물이라, 둘이 갈리면 원본에 물어야 한다. 그 원본이 JUSO 다.

이 모듈이 파는 것은 두 가지다.
  1) `NameIndex`  JUSO 가 주는 **행정동 이름**(hemdNm) → 우리 행정동코드
  2) `Juso`       조회 클라이언트 (판정 캐시 공유)

쓰는 곳
  scripts/arbitrate_adm.py   지오코딩 캐시의 xcheck='mismatch' 교정 (학원·아파트·K-apt·경쟁사)
  scripts/load_school.py     좌표 PIP 로 배치된 학교 중 **낡은 폴리곤 동**에 떨어진 것 재판정

⚠️ 함정 — 전부 실측이고, 하나라도 빠뜨리면 조용히 틀린다
  * **JUSO 의 `admCd` 는 법정동코드다.** 행정동코드가 아니다. 둘 다 10자리 숫자라
    잘못 조인해도 예외가 안 나고 틀린 동에 값이 얹힌 채 지나간다.
    행정동은 `hemdNm`(**이름만**) 으로 오므로 이름 매칭이 유일한 경로다.
  * **`addInfoYn=Y` 가 없으면 `hemdNm` 이 아예 안 온다.** 에러가 아니라 필드가 없다.
  * **이름 정규화 2종이 없으면 절반이 날아간다** — 숫자 앞 '제'(`용호제2동`→`용호2동`),
    시군구 공백(`고양시 덕양구`→`고양시덕양구`). 실측 무보정 8/15 → 정규화 후 30/30.
  * **우리 경계에 없는 동은 채택하지 않는다.** 붙일 폴리곤이 없는데 옮기면 값이 증발한다.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
VERDICT = PROC / "juso_arbiter.json"
API = "https://business.juso.go.kr/addrlink/addrLinkApi.do"


def read_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("JUSO_KEY="):
                v = line.split("=", 1)[1].strip()
                if v and not v.startswith("<"):
                    return v
    raise SystemExit("[중단] .env 의 JUSO_KEY 가 없다. business.juso.go.kr 에서 발급할 것")


def norm(s: str) -> str:
    """행정동명 정규화 — 숫자 앞 '제' 제거 + 공백 제거 (ADDR_TO_ADM §5-1)."""
    return re.sub(r"제(?=\d)", "", (s or "").strip()).replace(" ", "")


class NameIndex:
    """code_map 기반: 행정동 이름 → 행정동코드. 전체명 우선, 실패 시 시군구 스코프."""

    def __init__(self, cmap: dict | None = None):
        if cmap is None:
            cmap = json.loads((PROC / "code_map.json").read_text(encoding="utf-8"))
        sido = {r["code"]: r["name"] for r in cmap["sido"]}
        sgg = {r["code"]: r for r in cmap["sgg"]}
        self.name_of = {r["code"]: r["name"] for r in cmap["adm"]}
        self.full: dict[str, set[str]] = {}
        self.scoped: dict[tuple[str, str], str] = {}
        self.universe: set[str] = set()
        for r in cmap["adm"]:
            sg = sgg.get(r["parent"])
            if not sg:
                continue
            self.universe.add(r["code"])
            key = norm(sido.get(sg["parent"], "") + sg["name"] + r["name"])
            self.full.setdefault(key, set()).add(r["code"])
            self.scoped[(sg["code"], norm(r["name"]))] = r["code"]

    def resolve(self, hemd: str, near_cd: str) -> tuple[str | None, str | None]:
        """near_cd = 현재 배정된 코드(시군구 스코프 판정에 쓴다)."""
        if not hemd:
            return None, None
        hit = self.full.get(norm(hemd))
        if hit and len(hit) == 1:
            return next(iter(hit)), "full"
        parts = hemd.split()
        if parts:
            cd = self.scoped.get((near_cd[:5], norm(parts[-1])))
            if cd:
                return cd, "sgg"
        if hit:
            same = [c for c in hit if c[:5] == near_cd[:5]]
            if len(same) == 1:
                return same[0], "full-sgg"
        return None, None


ROAD_CACHE = PROC / "juso_road_cache.json"


def dongs(hemd: str) -> set[str]:
    """행정동 이름 집합. `hemdNm` 은 '전농2동,휘경2동' 처럼 **여러 동**을 줄 수 있다.

    ⚠️ 공백을 먼저 지우면 안 된다 — '강원특별자치도 동해시 동호동' 이 한 덩어리가 되어
       마지막 토큰 추출이 통째로 먹힌다. 토큰을 자른 **뒤에** 정규화한다.
    """
    out = set()
    for part in (hemd or "").split(","):
        toks = part.strip().split()
        if toks:
            out.add(norm(toks[-1]))
    return out


def split_addr(a: str) -> tuple[str | None, int | None]:
    """'… 장신로147번길 5-12' → ('… 장신로147번길', 5)"""
    m = re.match(r"^(.*?(?:\d*번길|로|길))\s+(\d+)", a or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


class RoadIndex:
    """도로명 단위 JUSO 조회 — **심판의 오답을 거르는 2차 확인**.

    왜 필요한가 (2026-08-12 실측)
        행안부 원본에도 **한 번지만 틀린 레코드**가 있다. 광주 광산구 장신로147번길에서
        5-12 만 행정동 '하남동', 이웃(5-18·10·139)은 전부 '수완동'이었다. 좌표 PIP·법정동·
        현장 확인도 수완동. 심판을 무조건 채택하면 '생각하는황소 광주수완'이 하남동에
        계상된다 — 지도 마커는 수완동에 찍히는데 카운트만 옆 동에 잡히는 모양이 된다.

        전수 점검 결과 분쟁 1,743건 중 1,178건은 이웃도 같이 옮겨간 **진짜 경계 지연**이라
        채택이 맞았고, 247건이 이런 외톨이였다. 그래서 **규칙을 끄는 게 아니라 외톨이만**
        거른다.

    판정
        같은 도로명에서 본번이 가까운 이웃들의 JUSO 행정동을 본다.
          · 다수가 PIP 동  → 외톨이 (원본 오류 의심) → 채택하지 않는다
          · 그 밖         → 채택 (기존 규칙 그대로)

    ⚠️ 이웃은 **번호가 가까운 것만** 쓴다. 긴 도로는 실제로 여러 동을 지나므로
       도로명만 같다고 이웃 취급하면 멀쩡한 판정을 외톨이로 몰아 버린다.
    """

    NEAR = 5
    NEAR_WIDE = 20

    def __init__(self, key: str | None = None):
        self.key = key or read_key()
        self.s = requests.Session()
        self.cache: dict[str, list] = {}
        self._dirty = False
        if ROAD_CACHE.exists():
            self.cache = json.loads(ROAD_CACHE.read_text(encoding="utf-8")).get("data", {})

    def road(self, road: str) -> list[dict]:
        if road in self.cache:
            return self.cache[road]
        out = []
        try:
            r = self.s.get(API, params={
                "confmKey": self.key, "keyword": road, "resultType": "json",
                "addInfoYn": "Y", "hstryYn": "N", "currentPage": 1, "countPerPage": 100},
                timeout=25)
            res = r.json().get("results") or {}
            if (res.get("common") or {}).get("errorCode") in ("0", 0):
                for j in res.get("juso") or []:
                    try:
                        out.append({"mnnm": int(j.get("buldMnnm") or -1),
                                    "hemd": j.get("hemdNm") or ""})
                    except (TypeError, ValueError):
                        continue
        except Exception:  # noqa: BLE001
            out = []
        self.cache[road] = out
        self._dirty = True
        time.sleep(0.15)
        return out

    def is_outlier(self, addr: str, hemd: str, pip_name: str) -> tuple[bool, int, str | None]:
        """(외톨이인가, 이웃 표본 수, 이웃 다수 동). 표본이 없으면 (False, 0, None) —
        **모르면 기존 규칙을 따른다.** 근거 없이 되돌리지 않는다."""
        road, mnnm = split_addr(addr)
        if not road or mnnm is None:
            return False, 0, None
        rows = self.road(road)
        near = [g["hemd"] for g in rows
                if g["hemd"] and g["mnnm"] != mnnm and abs(g["mnnm"] - mnnm) <= self.NEAR]
        if not near:
            near = [g["hemd"] for g in rows
                    if g["hemd"] and g["mnnm"] != mnnm and abs(g["mnnm"] - mnnm) <= self.NEAR_WIDE]
        if not near:
            return False, 0, None
        cnt = Counter()
        for h in near:
            cnt.update(dongs(h))
        top, _ = cnt.most_common(1)[0]
        if top in dongs(hemd):
            return False, len(near), top
        return (top == norm(pip_name)), len(near), top

    def save(self):
        if not self._dirty:
            return
        from datetime import date
        ROAD_CACHE.write_text(json.dumps(
            {"meta": {"updated": date.today().isoformat(), "source": "JUSO addrLinkApi (road)",
                      "count": len(self.cache)}, "data": self.cache}, ensure_ascii=False),
            encoding="utf-8")


class Juso:
    """JUSO 조회 + 판정 캐시. 캐시는 주소 → {hemd} 이며 재조회를 막는다."""

    def __init__(self, key: str | None = None, use_cache: bool = True):
        self.key = key or read_key()
        self.s = requests.Session()
        self.cache: dict[str, dict] = {}
        self._dirty = False
        if use_cache and VERDICT.exists():
            self.cache = json.loads(VERDICT.read_text(encoding="utf-8")).get("data", {})

    def hemd(self, addr: str, retry: int = 2) -> str | None:
        addr = (addr or "").strip()
        if not addr:
            return None
        if addr in self.cache:
            return (self.cache[addr] or {}).get("hemd")
        got = None
        for i in range(retry + 1):
            try:
                r = self.s.get(API, params={
                    "confmKey": self.key, "keyword": addr, "resultType": "json",
                    # ⚠️ 이게 없으면 hemdNm 이 안 온다
                    "addInfoYn": "Y", "currentPage": 1, "countPerPage": 1}, timeout=20)
                res = r.json().get("results") or {}
                if (res.get("common") or {}).get("errorCode") in ("0", 0):
                    js = res.get("juso") or []
                    got = (js[0].get("hemdNm") if js else None)
                break
            except Exception:
                time.sleep(0.5 * (i + 1))
        self.cache[addr] = {"hemd": got}
        self._dirty = True
        return got

    def save(self):
        if not self._dirty:
            return
        from datetime import date
        VERDICT.write_text(json.dumps(
            {"meta": {"updated": date.today().isoformat(),
                      "source": "JUSO addrLinkApi", "count": len(self.cache)},
             "data": self.cache}, ensure_ascii=False), encoding="utf-8")


def stale_zones() -> set[str]:
    """지오코딩 캐시의 교정 이력에서 **낡은 것으로 확인된 폴리곤 동**을 뽑는다.

    좌표로 직접 배치하는 원천(학교)은 xcheck 가 없어 스스로 분쟁을 알 수 없다.
    대신 주소 기반 교정이 남긴 증거를 빌려, 그 동에 떨어진 건만 재판정한다.
    """
    p = PROC / "geocode_cache.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8")).get("data") or {}
    return {r["adm_cd_pip"] for r in data.values()
            if r and r.get("arbiter") == "juso" and r.get("adm_cd_pip")}
