# -*- coding: utf-8 -*-
"""
geo_snapshot.py — 경계 원천 보존 + 코드 브리지 추출

배경
  행정동 경계 원천(vuski/admdongkor)은 개인이 유지보수하는 오픈소스다.
  리포가 중단·삭제되면 다음 행정구역 개편 때 대안이 없다.
  대안 1순위인 SGIS(국가데이터처)는 코드 체계가 통계청 8자리라
  본 프로젝트(행안부 체계)와 직접 조인되지 않는다.

  그런데 두 체계의 1:1 대응표는 admdongkor 원본 안에만 들어 있다
  (adm_cd = 통계청 8자리 / adm_cd2 = 행안부 10자리).
  시도 앞자리조차 다르므로(부산: 통계청 21 vs 행안부 26) 규칙 유도가 불가능하다.
  → 원천이 사라지면 대안으로 갈아탈 다리도 같이 사라진다. 지금 뽑아 둬야 한다.

산출물
  data/snapshot/geo/HangJeongDong_ver<VER>.geojson.gz   원본 스냅샷(gzip)
  data/snapshot/geo/MANIFEST.json                       sha256·출처·검증정보
  data/processed/code_bridge.json                       통계청 8 ↔ 행안부 10 브리지

사용법
  python geo_snapshot.py              # 스냅샷 + 브리지 생성
  python geo_snapshot.py --verify     # 기존 스냅샷 무결성만 검증
  python geo_snapshot.py --ver 20260701
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, 'data', 'raw', 'geo')
SNAP_DIR = os.path.join(ROOT, 'data', 'snapshot', 'geo')
PROC_DIR = os.path.join(ROOT, 'data', 'processed')

DEFAULT_VER = '20260701'
SOURCE_URL = ('https://raw.githubusercontent.com/vuski/admdongkor/master/'
              'ver{v}/HangJeongDong_ver{v}.geojson')


def log(*a):
    print('[geo_snapshot]', *a, flush=True)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- snapshot
def make_snapshot(ver: str) -> dict:
    src = os.path.join(RAW_DIR, f'HangJeongDong_ver{ver}.geojson')
    if not os.path.exists(src):
        sys.exit(f'[error] 원본 없음: {src}\n        scripts/geo_build.py 를 먼저 실행해 캐시를 채울 것.')

    os.makedirs(SNAP_DIR, exist_ok=True)
    dst = os.path.join(SNAP_DIR, f'HangJeongDong_ver{ver}.geojson.gz')

    digest = sha256_of(src)
    raw_size = os.path.getsize(src)
    log(f'원본 {raw_size:,} bytes · sha256 {digest[:16]}…')

    # gzip 압축 저장 (원본 sha256 을 매니페스트에 남겨 복원 후 검증 가능)
    with open(src, 'rb') as fi, gzip.open(dst, 'wb', compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo, length=1 << 20)
    gz_size = os.path.getsize(dst)
    log(f'스냅샷 {gz_size:,} bytes (압축률 {gz_size / raw_size * 100:.1f}%) → {dst}')

    return {'src': src, 'dst': dst, 'sha256': digest,
            'raw_size': raw_size, 'gz_size': gz_size}


def verify_snapshot(ver: str) -> bool:
    man_path = os.path.join(SNAP_DIR, 'MANIFEST.json')
    if not os.path.exists(man_path):
        log('[warn] MANIFEST.json 없음 — 검증 불가')
        return False
    man = json.load(open(man_path, encoding='utf-8'))
    gz = os.path.join(SNAP_DIR, os.path.basename(man['snapshot_file']))
    if not os.path.exists(gz):
        log(f'[FAIL] 스냅샷 파일 없음: {gz}')
        return False

    h = hashlib.sha256()
    with gzip.open(gz, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    ok = h.hexdigest() == man['sha256_uncompressed']
    log(('[OK]' if ok else '[FAIL]') + f" 무결성 검증 — {man['snapshot_file']}")
    if not ok:
        log(f"   기대 {man['sha256_uncompressed'][:16]}… / 실제 {h.hexdigest()[:16]}…")
    return ok


# ---------------------------------------------------------------- bridge
def build_bridge(ver: str) -> dict:
    src = os.path.join(RAW_DIR, f'HangJeongDong_ver{ver}.geojson')
    d = json.load(open(src, encoding='utf-8'))
    feats = d['features']

    kostat2mois, mois2kostat, entries = {}, {}, {}
    for f in feats:
        p = f['properties']
        k8 = p['adm_cd']          # 통계청 행정구역분류 8자리
        m10 = p['adm_cd2']        # 행안부 행정동코드 10자리
        kostat2mois[k8] = m10
        mois2kostat[m10] = k8
        entries[m10] = {
            'kostat8': k8,
            'mois10': m10,
            'mois8': m10[:8],     # 본 프로젝트 경계 코드 (geo_build.py 가 쓰는 값)
            'name': p['adm_nm'].split()[-1],
            'full_name': p['adm_nm'],
            'sgg': p['sgg'],
            'sido': p['sido'],
        }

    # 1:1 보장 검증 — 깨지면 브리지로 쓸 수 없다
    assert len(kostat2mois) == len(mois2kostat) == len(feats), (
        f'1:1 아님 — kostat {len(kostat2mois)} / mois {len(mois2kostat)} / feats {len(feats)}')

    same_sido = sum(1 for k, m in kostat2mois.items() if k[:2] == m[:2])
    sido_pairs = sorted({(m[:2], k[:2]) for k, m in kostat2mois.items()})

    log(f'브리지 {len(entries):,}행 · 1:1 검증 통과')
    log(f'시도 앞2자리 일치 {same_sido}/{len(entries)} '
        f'({same_sido / len(entries) * 100:.0f}%) — 규칙 유도 불가 확인')

    return {
        'meta': {
            'purpose': '통계청 행정구역분류(8) ↔ 행안부 행정동코드(10) 1:1 브리지',
            'why': ('경계 원천 admdongkor 이 중단될 경우 대안(SGIS·국가데이터처)은 통계청 코드 체계라 '
                    '행안부 체계(인구·juso admCd·인허가)와 직접 조인되지 않는다. '
                    '이 대응표는 admdongkor 원본에만 존재하므로 원천 소멸 전에 추출해 둔다.'),
            'source': SOURCE_URL.format(v=ver),
            'boundary_version': ver,
            'count': len(entries),
            'one_to_one': True,
            'sido_prefix_match': f'{same_sido}/{len(entries)}',
            'note': ('kostat8 과 mois10 은 시도 앞자리부터 다르다(예: 부산 통계청 21 / 행안부 26). '
                     'mois8 은 mois10 앞 8자리로, geo_build.py 가 경계 code 로 쓰는 값이다.'),
        },
        'sido_prefix_pairs': [{'mois2': m, 'kostat2': k} for m, k in sido_pairs],
        'kostat8_to_mois10': kostat2mois,
        'mois10_to_kostat8': mois2kostat,
        'entries': entries,
    }


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ver', default=DEFAULT_VER)
    ap.add_argument('--verify', action='store_true', help='기존 스냅샷 무결성만 검증')
    args = ap.parse_args()

    if args.verify:
        sys.exit(0 if verify_snapshot(args.ver) else 1)

    snap = make_snapshot(args.ver)
    bridge = build_bridge(args.ver)

    os.makedirs(PROC_DIR, exist_ok=True)
    bpath = os.path.join(PROC_DIR, 'code_bridge.json')
    with open(bpath, 'w', encoding='utf-8') as f:
        json.dump(bridge, f, ensure_ascii=False, indent=1)
    log(f'브리지 저장 → {bpath} ({os.path.getsize(bpath):,} bytes)')

    manifest = {
        'purpose': '행정동 경계 원천 스냅샷 — 원천(개인 유지보수 오픈소스) 소멸 대비',
        'snapshot_file': os.path.basename(snap['dst']),
        'boundary_version': args.ver,
        'source_url': SOURCE_URL.format(v=args.ver),
        'source_type': '오픈소스(개인 유지보수) — 공공기관 제공 아님. 출처 표기 시 주의',
        'sha256_uncompressed': snap['sha256'],
        'size_uncompressed': snap['raw_size'],
        'size_compressed': snap['gz_size'],
        'features': bridge['meta']['count'],
        'crs': 'CRS84 / WGS84 (EPSG:4326)',
        'verify_cmd': 'python scripts/geo_snapshot.py --verify',
        'restore': f"gzip -d -c {os.path.basename(snap['dst'])} > data/raw/geo/HangJeongDong_ver{args.ver}.geojson",
        'DO_NOT_DELETE': ('이 스냅샷은 재다운로드로 복구되지 않을 수 있다. '
                          'GitHub 원천이 사라지면 이 파일이 유일본이 된다.'),
    }
    mpath = os.path.join(SNAP_DIR, 'MANIFEST.json')
    with open(mpath, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    log(f'매니페스트 저장 → {mpath}')

    log('검증 재실행 중…')
    ok = verify_snapshot(args.ver)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
