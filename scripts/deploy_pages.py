# -*- coding: utf-8 -*-
"""
deploy_pages.py — 대시보드를 GitHub Pages 로 발행한다.

  로컬                          →  원격 (jichang999-dotcom/tes2)
  web/index.html                →  jiyeok/web/index.html
  web/config.json               →  jiyeok/web/config.json
  web/vendor/**                 →  jiyeok/web/vendor/**
  data/geo/*.geojson            →  jiyeok/data/geo/*.geojson
  data/processed/<필요분>.json  →  jiyeok/data/processed/*.json

  공개 URL:  https://jichang999-dotcom.github.io/tes2/jiyeok/web/

왜 이 구조인가
--------------
`index.html` 이 데이터를 `../data/geo/` · `../data/processed/` 로 읽는다. 그래서
`web/` 과 `data/` 의 **상대 위치를 로컬과 똑같이** 올린다. 경로를 예쁘게 만들려고
배포 때 HTML 을 고쳐 쓰면 **로컬에서 검증한 것과 다른 파일이 서비스된다** —
`verify_dashboard.js` 가 통과한 그 파일이 그대로 올라가는 게 낫다.

왜 파일별 PUT 이 아니라 Tree API 인가
------------------------------------
Contents API 는 **파일 하나당 커밋 하나**다. 532개면 커밋 532개에 API 호출 532회고,
중간에 하나 실패하면 **절반만 올라간 상태**로 남는다(그 상태가 곧 서비스된다).
Git Data API 는 blob 을 다 만든 뒤 **트리·커밋을 한 번에** 올리므로,
실패하면 아무것도 안 바뀌고 성공하면 전부 바뀐다.

⚠️ 이 대시보드는 **공개된다** (2026-08-05 관리자 결정).
   - `web/config.json` 의 **VWorld 키가 공개**된다. WMTS 는 Referer 를 강제하지
     않으므로(실측) 남이 우리 한도를 쓸 수 있다. 한도 소진이 의심되면 키를 재발급할 것.
   - 자사 149 · 경쟁사 388 지점 위치와 상권 분석이 **경쟁사에게도 보인다.**
   - 그래서 `data/processed` 를 통째로 올리지 않는다. 화면이 **실제로 읽는 9종 + poi**
     만 올린다. `geocode_cache.json`(23MB·주소 원문) 같은 중간 산출물은 제외다.

⚠️ `web/mock/` 은 올리지 않는다. 화면에 "실데이터 실패 시 mock 폴백"이 있어서,
   서버에서 경로가 어긋나면 **가짜 데이터가 그려진다.** 경고 배지가 뜨긴 하지만
   애초에 올리지 않으면 그 경로 자체가 없다 — 대신 "데이터 없음"으로 눈에 띄게 깨진다.

사용
----
  python scripts/deploy_pages.py --dry-run   # 무엇이 바뀌는지만 (업로드 없음)
  python scripts/deploy_pages.py             # 발행
  python scripts/deploy_pages.py --force     # 변경 감지 무시하고 전량 재업로드
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OWNER, REPO, BRANCH = "jichang999-dotcom", "tes2", "main"  # 공개 리포 (2026-09-17 tes1 에서 이전)
PREFIX = "jiyeok"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
PAGES_URL = f"https://{OWNER}.github.io/{REPO}/{PREFIX}/web/"

# 화면이 실제로 fetch 하는 것만. 늘리기 전에 index.html 의 load()/mergeCompetitor() 확인할 것
PROC_FILES = [
    "metrics.json", "metrics_sgg.json", "metrics_sido.json",
    "competitor_adm.json", "competitor_sgg.json", "competitor_sido.json",
    "population.json", "population_sgg.json", "population_sido.json",
    "apartment.json", "kapt.json",
]


def read_token() -> str:
    # 토큰은 test/.env 에 있다 (여러 프로젝트가 공유)
    for env in (ROOT.parent / ".env", ROOT / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GITHUB_TOKEN="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v and not v.startswith("<"):
                        return v
    raise SystemExit("[중단] GITHUB_TOKEN 을 찾지 못했다 (test/.env)")


def manifest() -> list[tuple[Path, str]]:
    """(로컬경로, 원격경로) 목록. 없는 파일은 즉시 중단한다."""
    out: list[tuple[Path, str]] = []

    def add(p: Path, remote: str):
        if not p.is_file():
            raise SystemExit(f"[중단] 배포 대상이 없다: {p}")
        out.append((p, f"{PREFIX}/{remote}"))

    add(ROOT / "web/index.html", "web/index.html")
    cfg = ROOT / "web/config.json"
    if cfg.is_file():
        add(cfg, "web/config.json")
    else:
        print("⚠️ web/config.json 이 없다 — 위성(VWorld) 버튼이 잠긴 채 발행된다")
    for p in sorted((ROOT / "web/vendor").rglob("*")):
        if p.is_file():
            add(p, "web/" + p.relative_to(ROOT / "web").as_posix())
    for f in PROC_FILES:
        add(ROOT / "data/processed" / f, f"data/processed/{f}")
    for p in sorted((ROOT / "data/processed").glob("web_*.json")):
        add(p, f"data/processed/{p.name}")
    for p in sorted((ROOT / "data/processed").glob("poi_*.json")):
        add(p, f"data/processed/{p.name}")
    for p in sorted((ROOT / "data/geo").glob("*.geojson")):
        add(p, f"data/geo/{p.name}")
    return out


def git_blob_sha(data: bytes) -> str:
    """git 이 매기는 blob sha1 — 원격 트리와 비교해 안 바뀐 파일을 건너뛴다."""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


class GH:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def req(self, method: str, url: str, retry: int = 3, **kw):
        for i in range(retry + 1):
            r = self.s.request(method, url, timeout=120, **kw)
            if r.status_code < 300:
                return r.json() if r.text else {}
            # 2차 레이트리밋·일시 오류는 물러섰다 재시도
            if r.status_code in (403, 429, 500, 502, 503) and i < retry:
                wait = 5 * (i + 1)
                print(f"    [{r.status_code}] {wait}s 대기 후 재시도")
                time.sleep(wait)
                continue
            raise SystemExit(f"[중단] {method} {url} → {r.status_code}\n{r.text[:400]}")
        raise SystemExit("[중단] 재시도 소진")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="변경 감지 무시하고 전량 재업로드")
    ap.add_argument("--message", default=None)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    payload = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_web_payload.py")],
        cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
    )
    if payload.stdout.strip():
        print(payload.stdout.strip())
    if payload.returncode:
        raise SystemExit("[중단] 웹 경량 번들 생성 실패\n" + payload.stderr[-1000:])

    files = manifest()
    total = sum(p.stat().st_size for p, _ in files)
    print(f"[deploy] 대상 {len(files):,}개 · {total/1024/1024:.1f}MB → {OWNER}/{REPO}:{BRANCH}/{PREFIX}")

    gh = GH(read_token())
    ref = gh.req("GET", f"{API}/git/ref/heads/{BRANCH}")
    base_commit = ref["object"]["sha"]
    base_tree = gh.req("GET", f"{API}/git/commits/{base_commit}")["tree"]["sha"]

    # 원격 현재 상태 — 안 바뀐 파일은 blob 을 다시 안 만든다
    remote: dict[str, str] = {}
    if not a.force:
        tree = gh.req("GET", f"{API}/git/trees/{base_tree}?recursive=1")
        if tree.get("truncated"):
            print("⚠️ 원격 트리가 잘렸다 — 변경 감지를 못 쓰고 전량 업로드한다")
        else:
            remote = {e["path"]: e["sha"] for e in tree.get("tree", [])
                      if e["type"] == "blob"}

    changed = []
    for p, rp in files:
        data = p.read_bytes()
        if not a.force and remote.get(rp) == git_blob_sha(data):
            continue
        changed.append((p, rp, data))

    print(f"[deploy] 변경 {len(changed):,}개 · 동일 {len(files)-len(changed):,}개")
    if not changed:
        print("[deploy] 바뀐 게 없다. 발행 생략")
        print(f"         {PAGES_URL}")
        return 0
    if a.dry_run:
        for _, rp, d in changed[:20]:
            print(f"    {len(d)/1024:8.0f}KB  {rp}")
        if len(changed) > 20:
            print(f"    ... 외 {len(changed)-20}개")
        print("[deploy] --dry-run 이라 업로드하지 않았다")
        return 0

    # 1) blob 생성
    entries = []
    for i, (p, rp, data) in enumerate(changed, 1):
        b = gh.req("POST", f"{API}/git/blobs", json={
            "content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": rp, "mode": "100644", "type": "blob", "sha": b["sha"]})
        if i % 50 == 0 or i == len(changed):
            print(f"    blob {i:,}/{len(changed):,}")

    # 2) 트리 → 3) 커밋 → 4) ref 이동 (여기까지 와야 원격이 바뀐다)
    #
    # ⚠️ 이 리포는 **여러 프로젝트가 공유하는 monorepo** 다 (단테 대시보드가 스케줄로
    #    같은 main 에 푸시한다). blob 532개를 올리는 동안 남이 먼저 커밋하면
    #    ref PATCH 가 422 "not a fast forward" 로 튕긴다.
    #    우리 파일은 `jiyeok/` 아래라 내용 충돌은 없다 — **새 head 위에 다시 얹으면 된다.**
    #    blob 은 이미 서버에 있으므로 재시도 비용은 트리·커밋 2회뿐이다.
    msg = a.message or f"지역분석 대시보드 발행 ({len(changed)}개 갱신)"
    for attempt in range(4):
        new_tree = gh.req("POST", f"{API}/git/trees",
                          json={"base_tree": base_tree, "tree": entries})["sha"]
        commit = gh.req("POST", f"{API}/git/commits",
                        json={"message": msg, "tree": new_tree, "parents": [base_commit]})
        r = gh.s.patch(f"{API}/git/refs/heads/{BRANCH}",
                       json={"sha": commit["sha"]}, timeout=120)
        if r.status_code < 300:
            break
        if r.status_code == 422 and "fast forward" in r.text.lower() and attempt < 3:
            print("    남이 먼저 푸시했다 — 최신 head 위로 다시 얹는다")
            time.sleep(3)
            ref = gh.req("GET", f"{API}/git/ref/heads/{BRANCH}")
            base_commit = ref["object"]["sha"]
            base_tree = gh.req("GET", f"{API}/git/commits/{base_commit}")["tree"]["sha"]
            continue
        raise SystemExit(f"[중단] ref 이동 실패 → {r.status_code}\n{r.text[:400]}")
    else:
        raise SystemExit("[중단] 재시도 4회 모두 충돌 — 잠시 뒤 다시 실행할 것")

    print(f"[deploy] 커밋 {commit['sha'][:7]} · {msg}")
    print(f"[deploy] ✅ {PAGES_URL}")
    print("         Pages 반영에 보통 40~60초 걸린다")
    print("⚠️ 공개 URL 이다 — VWorld 키와 경쟁사 분석이 함께 공개된다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
