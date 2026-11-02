# -*- coding: utf-8 -*-
"""
geo_build.py — 지역분석 프로젝트 / 팀원1 (지리·코드)

DATA_CONTRACT.md §2-1, §2-2 산출물 생성 전 과정(다운로드 → 디졸브 → 간소화 → 분할 → 저장).

산출물
  data/geo/sido.geojson
  data/geo/sgg.geojson
  data/geo/adm_<sgg_cd>.geojson   (시군구별 분할)
  data/geo/adm_index.json
  data/processed/code_map.json

원천
  1) vuski/admdongkor 행정동 경계 GeoJSON (WGS84)
     https://raw.githubusercontent.com/vuski/admdongkor/master/ver<VER>/HangJeongDong_ver<VER>.geojson
  2) 행정표준코드관리시스템 법정동코드 전체자료
     POST https://www.code.go.kr/etc/codeFullDown.do  (codeseId=법정동코드)

의존성: requests (표준 라이브러리 외). geopandas/shapely 사용하지 않음 — 순수 python 구현.

사용법
  python geo_build.py              # 전체 빌드
  python geo_build.py --sample     # 샘플(시군구 4개)만 빌드, 네트워크 캐시 사용
  python geo_build.py --ver 20260701
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, 'data', 'raw', 'geo')
GEO_DIR = os.path.join(ROOT, 'data', 'geo')
PROC_DIR = os.path.join(ROOT, 'data', 'processed')

DEFAULT_VER = '20260701'
ADM_URL = 'https://raw.githubusercontent.com/vuski/admdongkor/master/ver{v}/HangJeongDong_ver{v}.geojson'
BJD_URL = 'https://www.code.go.kr/etc/codeFullDown.do'
BJD_REFERER = 'https://www.code.go.kr/stdcode/regCodeL.do'

# 디졸브(경계 상쇄)용 좌표 양자화 자릿수. 출력 자릿수(5)보다 커야 원본 위상이 보존된다.
Q_TOPO = 6
# DATA_CONTRACT §2-1: 좌표 정밀도 소수점 5자리
Q_OUT = 5

SAMPLE_SGG = ['11110', '11680', '41135', '28185']

# 간소화 강도 후보(Visvalingam 삼각형 면적, deg^2). 작을수록 원본에 가까움.
ADM_TOLS = [0.0, 5e-10, 2e-9, 8e-9, 3e-8, 1e-7, 4e-7]
SGG_TOLS = [2e-9, 8e-9, 3e-8, 1e-7, 4e-7, 1.5e-6]
SIDO_TOLS = [8e-9, 3e-8, 1e-7, 4e-7, 1.5e-6, 6e-6]

BUDGET_ADM = 300 * 1024
BUDGET_SGG = 3 * 1024 * 1024
BUDGET_SIDO = 1 * 1024 * 1024


def log(*a):
    print('[geo_build]', *a, flush=True)


# ---------------------------------------------------------------- download
def download(url, path, post_data=None, referer=None, timeout=600):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        log('cache hit:', os.path.basename(path), f'{os.path.getsize(path):,} bytes')
        return path
    import requests
    os.makedirs(os.path.dirname(path), exist_ok=True)
    headers = {'User-Agent': 'Mozilla/5.0'}
    if referer:
        headers['Referer'] = referer
    log('download:', url)
    s = requests.Session()
    s.headers.update(headers)
    if referer:
        try:
            s.get(referer, timeout=60, verify=False)
        except Exception:
            pass
    if post_data is not None:
        r = s.post(url, data=post_data, timeout=timeout, verify=False)
    else:
        r = s.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        if post_data is not None:
            f.write(r.content)
        else:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(tmp, path)
    log('saved:', os.path.basename(path), f'{os.path.getsize(path):,} bytes')
    return path


# ---------------------------------------------------------------- geometry
def rings_of(geom):
    t = geom['type']
    polys = [geom['coordinates']] if t == 'Polygon' else geom['coordinates']
    for poly in polys:
        for ring in poly:
            yield ring


def clean_ring(ring, q):
    pts = [(round(p[0], q), round(p[1], q)) for p in ring]
    out = [pts[0]]
    for p in pts[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) < 3:
        return None
    if out[0] != out[-1]:
        out.append(out[0])
    if len(out) < 4:
        return None
    return out


def signed_area(ring):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += x1 * y2 - x2 * y1
    return s / 2.0


def dissolve(features):
    """행정동 폴리곤 집합을 경계 상쇄(edge cancellation)로 디졸브해 외곽 링 목록을 반환.

    인접 폴리곤이 동일한 정점열을 공유한다는 원천 데이터의 위상 특성을 이용한다.
    (admdongkor 데이터에서 검증됨 — 전 시군구/시도가 닫힌 링으로 복원됨)
    """
    edges = {}
    for f in features:
        for ring in rings_of(f['geometry']):
            cl = clean_ring(ring, Q_TOPO)
            if cl is None:
                continue
            for a, b in zip(cl, cl[1:]):
                if edges.pop((b, a), None) is not None:
                    continue
                edges[(a, b)] = 1
    adj = collections.defaultdict(list)
    for (a, b) in edges:
        adj[a].append(b)
    rings, used = [], set()
    for e in list(edges):
        if e in used:
            continue
        a, b = e
        used.add(e)
        ring = [a, b]
        prev, cur = a, b
        while cur != a:
            nxt = None
            for c in adj.get(cur, ()):
                if (cur, c) not in used and c != prev:
                    nxt = c
                    break
            if nxt is None:
                for c in adj.get(cur, ()):
                    if (cur, c) not in used:
                        nxt = c
                        break
            if nxt is None:
                break
            used.add((cur, nxt))
            ring.append(nxt)
            prev, cur = cur, nxt
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def rings_to_multipolygon(rings):
    """외곽/구멍을 부호 면적으로 판별해 MultiPolygon 좌표로 조립."""
    if not rings:
        return None
    info = [(r, signed_area(r)) for r in rings]
    info = [(r, a) for r, a in info if abs(a) > 0]
    if not info:
        return None
    outer_sign = 1 if max(info, key=lambda x: abs(x[1]))[1] > 0 else -1
    outers = [(r, a) for r, a in info if (1 if a > 0 else -1) == outer_sign]
    holes = [(r, a) for r, a in info if (1 if a > 0 else -1) != outer_sign]
    outers.sort(key=lambda x: -abs(x[1]))
    polys = [[orient(r, ccw=True)] for r, _ in outers]
    for hr, _ in holes:
        idx = find_container([(r, a) for r, a in outers], hr)
        if idx is None:
            continue
        polys[idx].append(orient(hr, ccw=False))
    return polys


def orient(ring, ccw=True):
    a = signed_area(ring)
    if (a > 0) != ccw:
        return ring[::-1]
    return ring


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def find_container(outers, hole):
    pt = hole[0]
    best, best_area = None, None
    for i, (r, a) in enumerate(outers):
        if point_in_ring(pt, r):
            if best is None or abs(a) < best_area:
                best, best_area = i, abs(a)
    return best


# ---------------------------------------------------------------- simplify
def tri_area(p, q, r):
    return abs((q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])) / 2.0


def simplify_ring(ring, tol, min_pts=5, max_pass=12):
    """Visvalingam-Whyatt 단일패스 반복.

    삼각형 면적은 정점열의 진행 방향과 무관하므로, 인접 폴리곤이 공유하는
    경계선에서는 양쪽이 동일한 제거 판정을 내려 위상 어긋남(슬리버)이 최소화된다.
    (Douglas-Peucker 는 방향 의존적이라 공유 경계가 갈라짐)
    """
    if tol <= 0:
        return ring
    pts = ring[:-1]
    for _ in range(max_pass):
        n = len(pts)
        if n <= min_pts:
            break
        keep = [True] * n
        removed = 0
        i = 0
        while i < n:
            if not keep[i]:
                i += 1
                continue
            p = pts[(i - 1) % n]
            q = pts[i]
            r = pts[(i + 1) % n]
            if tri_area(p, q, r) < tol:
                keep[i] = False
                removed += 1
                i += 2  # 인접 정점 동시 제거 방지
                if n - removed <= min_pts:
                    break
            else:
                i += 1
        if not removed:
            break
        pts = [p for p, k in zip(pts, keep) if k]
    out = pts + [pts[0]]
    if len(out) < 4:
        return None
    return out


def round_ring(ring, q=Q_OUT):
    pts = [(round(x, q), round(y, q)) for x, y in ring]
    out = [pts[0]]
    for p in pts[1:]:
        if p != out[-1]:
            out.append(p)
    if out[0] != out[-1]:
        out.append(out[0])
    if len(out) < 4:
        return None
    return [[x, y] for x, y in out]


def simplify_multipolygon(polys, tol):
    res = []
    for poly in polys:
        rings = []
        for j, ring in enumerate(poly):
            s = simplify_ring(list(map(tuple, ring)), tol)
            if s is None:
                if j == 0:
                    rings = []
                    break
                continue
            r = round_ring(s)
            if r is None:
                if j == 0:
                    rings = []
                    break
                continue
            rings.append(r)
        if rings:
            res.append(rings)
    return res


def geom_to_polys(geom):
    if geom['type'] == 'Polygon':
        return [geom['coordinates']]
    return geom['coordinates']


# ---------------------------------------------------------------- io
def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    txt = dumps(obj)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)
    return len(txt.encode('utf-8'))


def fc(features):
    return {'type': 'FeatureCollection', 'features': features}


def feat(code, name, parent, polys):
    return {
        'type': 'Feature',
        'properties': {'code': code, 'name': name, 'parent': parent},
        'geometry': {'type': 'MultiPolygon', 'coordinates': polys},
    }


def pick_tol(build_fn, tols, budget, label):
    """예산(byte)을 만족하는 가장 약한 간소화 강도를 선택."""
    last = None
    for t in tols:
        obj = build_fn(t)
        size = len(dumps(obj).encode('utf-8'))
        last = (t, obj, size)
        if size <= budget:
            log(f'  {label}: tol={t:g} size={size:,}B OK')
            return obj, t, size
        log(f'  {label}: tol={t:g} size={size:,}B > budget, 강도 상향')
    log(f'  {label}: 예산 미달성 — 최강 강도 채택 size={last[2]:,}B')
    return last[1], last[0], last[2]


# ---------------------------------------------------------------- bjd
def load_bjd(zip_path):
    """법정동코드 전체자료 → [(code10, full_name)] (존재 & 읍면동 레벨)."""
    out = []
    with zipfile.ZipFile(zip_path) as z:
        name = [n for n in z.namelist() if n.endswith('.txt')][0]
        text = z.read(name).decode('cp949')
    for line in text.splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        code, nm, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if len(code) != 10 or status != '존재':
            continue
        out.append((code, nm))
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ver', default=DEFAULT_VER)
    ap.add_argument('--sample', action='store_true', help='시군구 4개만 빌드(형식 동일)')
    ap.add_argument('--skip-bjd', action='store_true')
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(GEO_DIR, exist_ok=True)
    os.makedirs(PROC_DIR, exist_ok=True)

    adm_path = os.path.join(RAW_DIR, f'HangJeongDong_ver{args.ver}.geojson')
    download(ADM_URL.format(v=args.ver), adm_path)

    bjd_zip = os.path.join(RAW_DIR, 'bjd_full.zip')
    if not args.skip_bjd:
        try:
            download(BJD_URL, bjd_zip, post_data={'codeseId': '법정동코드'}, referer=BJD_REFERER)
        except Exception as e:
            log('법정동코드 다운로드 실패:', e)

    log('parse', os.path.basename(adm_path))
    with open(adm_path, encoding='utf-8') as f:
        src = json.load(f)
    feats = src['features']
    if args.sample:
        feats = [f for f in feats if f['properties']['sgg'] in SAMPLE_SGG]
    log('features:', len(feats))

    # ---- 코드 트리
    sido_nm, sgg_nm, adm_rows = {}, {}, []
    by_sgg = collections.defaultdict(list)
    for f in feats:
        p = f['properties']
        adm8 = p['adm_cd2'][:8]
        sgg = p['sgg']
        sido = p['sido']
        assert adm8[:5] == sgg, (adm8, sgg)
        sido_nm[sido] = p['sidonm']
        sgg_nm[sgg] = (p['sggnm'], sido)
        adm_rows.append((adm8, p['adm_nm'].split()[-1], sgg))
        by_sgg[sgg].append(f)

    log(f'sido={len(sido_nm)} sgg={len(sgg_nm)} adm={len(adm_rows)}')

    # ---- 시도 / 시군구 경계 (원본에서 디졸브)
    log('dissolve → sgg')
    sgg_polys = {}
    for code, group in by_sgg.items():
        polys = rings_to_multipolygon(dissolve(group))
        if polys is None:
            log('  WARN dissolve 실패 sgg', code)
            continue
        sgg_polys[code] = polys

    log('dissolve → sido')
    by_sido = collections.defaultdict(list)
    for f in feats:
        by_sido[f['properties']['sido']].append(f)
    sido_polys = {}
    for code, group in by_sido.items():
        polys = rings_to_multipolygon(dissolve(group))
        if polys is None:
            log('  WARN dissolve 실패 sido', code)
            continue
        sido_polys[code] = polys

    # ---- 간소화 + 저장
    log('simplify/write sido.geojson')

    def build_sido(tol):
        return fc([feat(c, sido_nm[c], None, simplify_multipolygon(p, tol))
                   for c, p in sorted(sido_polys.items())])

    obj, tol_sido, size_sido = pick_tol(build_sido, SIDO_TOLS, BUDGET_SIDO, 'sido')
    write_json(os.path.join(GEO_DIR, 'sido.geojson'), obj)

    log('simplify/write sgg.geojson')

    def build_sgg(tol):
        return fc([feat(c, sgg_nm[c][0], sgg_nm[c][1], simplify_multipolygon(p, tol))
                   for c, p in sorted(sgg_polys.items())])

    obj, tol_sgg, size_sgg = pick_tol(build_sgg, SGG_TOLS, BUDGET_SGG, 'sgg')
    write_json(os.path.join(GEO_DIR, 'sgg.geojson'), obj)

    # ---- 행정동: 시군구 단위 분할
    log('simplify/write adm_<sgg_cd>.geojson')
    # 기존 분할 파일 정리(버전 변경 시 잔존 방지)
    if not args.sample:
        for fn in os.listdir(GEO_DIR):
            if fn.startswith('adm_') and fn.endswith('.geojson'):
                os.remove(os.path.join(GEO_DIR, fn))

    adm_index = {}
    over_budget = []
    tol_used = collections.Counter()
    for sgg, group in sorted(by_sgg.items()):
        base = []
        for f in group:
            p = f['properties']
            base.append((p['adm_cd2'][:8], p['adm_nm'].split()[-1], geom_to_polys(f['geometry'])))
        chosen = None
        for tol in ADM_TOLS:
            o = fc([feat(c, n, sgg, simplify_multipolygon(pp, tol)) for c, n, pp in base])
            size = len(dumps(o).encode('utf-8'))
            chosen = (tol, o, size)
            if size <= BUDGET_ADM:
                break
        tol, o, size = chosen
        tol_used[tol] += 1
        if size > BUDGET_ADM:
            over_budget.append((sgg, size))
        write_json(os.path.join(GEO_DIR, f'adm_{sgg}.geojson'), o)
        adm_index[sgg] = [ft['properties']['code'] for ft in o['features']]

    write_json(os.path.join(GEO_DIR, 'adm_index.json'), adm_index)
    log('adm tol 분포:', dict(tol_used))
    if over_budget:
        log('WARN 300KB 초과 시군구:', over_budget)

    # ---- code_map.json
    log('build code_map.json')
    code_map = {
        'meta': {
            'updated': time.strftime('%Y-%m-%d'),
            'boundary_source': ADM_URL.format(v=args.ver),
            'boundary_version': args.ver,
            'bjd_source': 'https://www.code.go.kr (법정동코드 전체자료)',
            'note': 'bjd2adm 은 공식 행정동↔법정동 매핑 원천을 확보하지 못해 비어 있음. '
                    'bjd2adm_draft 는 동일 시군구 내 명칭 기반으로 추정한 참고용 초안이며 검증되지 않음.',
        },
        'sido': [{'code': c, 'name': sido_nm[c]} for c in sorted(sido_nm)],
        'sgg': [{'code': c, 'name': sgg_nm[c][0], 'parent': sgg_nm[c][1]} for c in sorted(sgg_nm)],
        'adm': [{'code': c, 'name': n, 'parent': s} for c, n, s in sorted(adm_rows)],
        'bjd2adm': {},
        'bjd2adm_draft': {},
    }

    if os.path.exists(bjd_zip):
        try:
            bjd = load_bjd(bjd_zip)
            draft, matched = build_draft_mapping(bjd, adm_rows, set(sgg_nm))
            code_map['bjd2adm_draft'] = draft
            code_map['meta']['bjd2adm_draft_coverage'] = matched
            log('bjd2adm_draft:', matched)
        except Exception as e:
            log('bjd 처리 실패:', e)

    size_cm = write_json(os.path.join(PROC_DIR, 'code_map.json'), code_map)
    log(f'code_map.json {size_cm:,}B')
    log(f'done in {time.time()-t0:.1f}s')


def build_draft_mapping(bjd_rows, adm_rows, sgg_set):
    """참고용 초안: 같은 시군구 안에서 법정동명 ↔ 행정동명(숫자/제N 제거) 일치로 추정."""
    import re

    def base(nm):
        nm = re.sub(r'제?\d+동$', '동', nm)
        nm = re.sub(r'[·.]', '', nm)
        return nm

    adm_by_sgg = collections.defaultdict(list)
    for code, name, sgg in adm_rows:
        adm_by_sgg[sgg].append((code, base(name)))

    draft = {}
    total = 0
    hit = 0
    for code10, full in bjd_rows:
        sgg = code10[:5]
        if sgg not in sgg_set:
            continue
        if code10[5:8] == '000' or code10[8:] != '00':
            continue  # 시군구 레벨 / 리 레벨 제외
        total += 1
        nm = base(full.split()[-1])
        cands = [c for c, b in adm_by_sgg.get(sgg, []) if b == nm]
        if not cands:
            stem = re.sub(r'(\d*[가동읍면리])$', '', nm)
            if len(stem) >= 2:
                cands = [c for c, b in adm_by_sgg.get(sgg, []) if stem in b]
        if cands:
            draft[code10] = sorted(cands)
            hit += 1
    return draft, {'bjd_total': total, 'matched': hit,
                   'ratio': round(hit / total, 4) if total else 0}


if __name__ == '__main__':
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    sys.exit(main())
