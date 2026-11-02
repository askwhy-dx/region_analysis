# -*- coding: utf-8 -*-
"""
make_web_config.py — `.env` 의 브라우저용 키를 `web/config.json` 으로 내보낸다.

  python scripts/make_web_config.py

대시보드는 정적 HTML 이라 `.env` 를 못 읽는다. 위성지도(VWorld)처럼 **브라우저가
직접 호출하는 것**만 이 파일로 넘긴다.

⚠️ **`web/config.json` 은 공유·업로드 대상이 아니다.**
   VWorld 키는 도메인 등록으로 보호되지만(등록한 도메인 밖에서는 거절된다)
   그래도 공개 저장소에 올리지 말 것. 깃허브 업로드 스크립트도 이 파일을 제외한다.

⚠️ 여기에 **DB 접속정보·포털 서비스키 같은 서버용 비밀은 절대 넣지 않는다.**
   브라우저로 나가는 순간 공개된 것과 같다. VWorld 키만 예외인 이유는
   그것이 '도메인이 고정된 지도 타일용 공개 키'이기 때문이다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "config.json"

# .env 키 이름 → config.json 필드명 (브라우저로 나가도 되는 것만)
EXPORT = {"VWORLD_KEY": "vworldKey"}


def load_env() -> dict[str, str]:
    f = ROOT / ".env"
    env: dict[str, str] = {}
    if not f.exists():
        return env
    for line in f.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    env = load_env()
    cfg = {dst: env[src] for src, dst in EXPORT.items() if env.get(src)}
    # ⚠️ 키가 없어도 **빈 파일을 만든다.** 파일이 없으면 브라우저 콘솔에 404 가 찍히고,
    #    상담 중 개발자도구를 연 사람에게는 "뭔가 깨졌다"로 보인다. 빈 `{}` 면 조용하다.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if not cfg:
        OUT.write_text("{}\n", encoding="utf-8")
        print("[web] .env 의 VWORLD_KEY 가 비어 있다 → 빈 config.json 을 만들었다")
        print("      (대시보드의 VWorld 버튼은 🔒 잠김으로 보인다)")
        print("      https://www.vworld.kr/dev/v4api.do 에서 인증키 신청 후")
        print("      **서비스 도메인에 localhost 를 등록**할 것 (등록 안 하면 타일이 거절된다)")
        return 0

    OUT.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[web] → {OUT}  ({', '.join(cfg)})")
    print("[web] 대시보드에서 '위성(VWorld)' 버튼이 켜진다. 새로고침할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
