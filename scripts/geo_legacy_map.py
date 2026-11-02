# -*- coding: utf-8 -*-
"""
geo_legacy_map.py — 지역분석 프로젝트 / 팀원1 (지리·코드)

구체계 행정동/시군구/시도 코드 → 현행(2026-07-01 전남광주통합 반영) 코드 변환표 생성.

배경
  경계·코드 정본(code_map.json)은 2026-07-01 개편 후 체계(전남광주통합특별시 `12`).
  행안부 주민등록 인구통계 202606 등 개편 이전 통계는 구체계(광주 `29`, 전남 `46`)를 쓴다.
  또한 인천은 통합과 무관하게 2026 중 중구/동구/서구가 제물포구·영종구·서해구·검단구로 재편됐다.

입력
  좌변(구코드): data/raw/population/age_202606.csv   ← 팀원2 소유. **읽기 전용**
  우변(신코드): data/processed/code_map.json         ← 팀원1 소유

출력
  data/processed/code_legacy_map.json

원칙(정직성)
  - 명칭 1:1 유일 매칭만 채택한다. 후보가 0개이거나 2개 이상이면 **추측하지 않고** unmatched 로 뺀다.
  - 출장소(행정동 하위 단위)는 코드 개편이 아니므로 adm 매핑에 넣지 않고 별도 섹션으로 분리한다.

사용법
  python geo_legacy_map.py [--pop data/raw/population/age_202606.csv]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POP_DEFAULT = os.path.join(ROOT, 'data', 'raw', 'population', 'age_202606.csv')
CODE_MAP = os.path.join(ROOT, 'data', 'processed', 'code_map.json')
OUT = os.path.join(ROOT, 'data', 'processed', 'code_legacy_map.json')

ROW_PAT = re.compile(r'^(.*?)\s*\((\d{10})\)\s*$')
SIDO_SGG_OVERLAP_MIN = 0.8   # 시도 재편 후보 판정 최소 명칭 중복률

# ---------------------------------------------------------------------------
# 개칭(rename) 수기 등재분
#
#   행정동 이름 자체가 바뀐 경우는 명칭 매칭으로 원리적으로 못 잡는다
#   (좌변 '안양8동' ↔ 우변 '명학동' — 공통 문자열이 없다).
#   자동 매칭에 맡기면 영원히 unmatched 로 남아, 경계는 있는데 인구가 0인
#   백지 폴리곤이 된다.
#
#   ⚠️ 등재 기준 — 이 표는 §원칙(정직성)의 예외이므로 문턱을 높게 둔다.
#     1) 지자체 고시·조례로 확인된 건만 등재한다. 추측·추정 금지.
#     2) basis 에 근거 URL과 '관할구역 조정 여부'를 반드시 남긴다.
#        경계가 함께 바뀐 경우(분동·통합·구역조정)는 인구를 1:1로 옮기면
#        틀린 숫자가 되므로 여기 넣지 말고 unmatched 로 두어야 한다.
#     3) 대상 신코드가 code_map 에 없으면 조용히 넘어가지 않고 경고한다.
#
#   old_code: (new_code, 변경내용, 근거)
# ---------------------------------------------------------------------------
MANUAL_ADM = {
    '41171580': ('41171582', '안양8동 → 명학동',
                 '2026-07-01 시행 · 안양시 행정동 명칭변경(주민투표 찬성 85.3% → 시의회 의결). '
                 '명칭 변경만이며 관할구역 조정·분동·통합 없음 → 인구 1:1 이관 안전. '
                 'https://ngoanyang.or.kr/11265'),
    '41171581': ('41171583', '안양9동 → 병목안동',
                 '2026-07-01 시행 · 안양시 행정동 명칭변경(주민투표 찬성 79.8% → 시의회 의결). '
                 '명칭 변경만이며 관할구역 조정·분동·통합 없음 → 인구 1:1 이관 안전. '
                 'https://ngoanyang.or.kr/11265'),
}


def log(*a):
    print('[geo_legacy_map]', *a, flush=True)


def norm(name: str) -> str:
    """행정동 명칭 정규화. 통계 표기와 경계 데이터 표기 차이를 흡수한다.
      '창신제1동' → '창신1동' / '종로1.2.3.4가동' → '종로1·2·3·4가동'
    """
    s = name.strip().replace(' ', '')
    s = s.replace('.', '·').replace('・', '·').replace('ㆍ', '·')
    s = re.sub(r'제(\d+)', r'\1', s)
    return s


def load_old(path):
    """인구통계 CSV의 '행정구역' 컬럼 → 레벨별 구코드 사전."""
    sido, sgg, adm = {}, {}, {}
    with open(path, encoding='cp949', newline='') as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or not row:
                continue
            m = ROW_PAT.match(row[0].strip())
            if not m:
                log('  파싱 실패:', row[0][:40])
                continue
            full, code = m.group(1).strip(), m.group(2)
            parts = full.split()
            if code[2:] == '00000000':
                sido[code[:2]] = parts[-1]
            elif code[5:] == '00000':
                sgg[code[:5]] = ' '.join(parts[1:])   # '수원시 장안구' 형태 유지
            else:
                adm[code[:8]] = (parts[-1], full)
    return sido, sgg, adm


def load_new():
    cm = json.load(open(CODE_MAP, encoding='utf-8'))
    sido = {s['code']: s['name'] for s in cm['sido']}
    sgg = {s['code']: s['name'] for s in cm['sgg']}
    adm = {a['code']: a['name'] for a in cm['adm']}
    return sido, sgg, adm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pop', default=POP_DEFAULT)
    args = ap.parse_args()

    o_sido, o_sgg, o_adm = load_old(args.pop)
    n_sido, n_sgg, n_adm = load_new()
    log(f'구: sido={len(o_sido)} sgg={len(o_sgg)} adm={len(o_adm)}')
    log(f'신: sido={len(n_sido)} sgg={len(n_sgg)} adm={len(n_adm)}')

    evidence = {}

    # ---------------------------------------------------------- 1. 시도
    sido_map = {}
    new_by_name = collections.defaultdict(list)
    for c, n in n_sido.items():
        new_by_name[n].append(c)
    left_old = []
    for oc, on in o_sido.items():
        cand = new_by_name.get(on, [])
        if len(cand) == 1:
            sido_map[oc] = cand[0]
        else:
            left_old.append(oc)
    claimed = set(sido_map.values())
    left_new = [c for c in n_sido if c not in claimed]

    # 잔여 시도는 하위 시군구 명칭 중복률로 판정(근거 기록)
    o_sgg_names = collections.defaultdict(set)
    for c, n in o_sgg.items():
        o_sgg_names[c[:2]].add(norm(n))
    n_sgg_names = collections.defaultdict(set)
    for c, n in n_sgg.items():
        n_sgg_names[c[:2]].add(norm(n))
    for oc in left_old:
        best = None
        for nc in left_new:
            a, b = o_sgg_names[oc], n_sgg_names[nc]
            if not a:
                continue
            ratio = len(a & b) / len(a)
            if best is None or ratio > best[1]:
                best = (nc, ratio)
        if best and best[1] >= SIDO_SGG_OVERLAP_MIN:
            sido_map[oc] = best[0]
            evidence[f'sido:{oc}'] = {
                'old_name': o_sido[oc], 'new_code': best[0], 'new_name': n_sido[best[0]],
                'sgg_name_overlap': round(best[1], 4),
                'matched_sgg': sorted(o_sgg_names[oc] & n_sgg_names[best[0]]),
            }
            log(f'  시도 재편 판정: {oc} {o_sido[oc]} → {best[0]} {n_sido[best[0]]} '
                f'(시군구 명칭 중복률 {best[1]:.2%})')
        else:
            log(f'  시도 미매칭: {oc} {o_sido[oc]}')

    # ---------------------------------------------------------- 2. 시군구
    sgg_map, sgg_unmatched, sgg_na = {}, [], []
    # 실제로 하위 행정동을 거느린 구코드만 '진짜 시군구'로 본다.
    # (통합시의 시 레벨 행(41110 수원시)이나 출장소 행(41222 송탄출장소)은 매핑 대상이 아님)
    o_sgg_with_adm = {c[:5] for c in o_adm}
    n_sgg_by_sido = collections.defaultdict(lambda: collections.defaultdict(list))
    for c, n in n_sgg.items():
        n_sgg_by_sido[c[:2]][norm(n)].append(c)
    for oc, on in sorted(o_sgg.items()):
        if oc not in o_sgg_with_adm:
            sgg_na.append(f'{oc} {on}'.strip())
            continue
        ns = sido_map.get(oc[:2])
        if ns is None:
            sgg_unmatched.append(f'{oc} {on} (상위 시도 미매칭)')
            continue
        cand = n_sgg_by_sido[ns].get(norm(on), [])
        if len(cand) == 1:
            sgg_map[oc] = cand[0]
        elif oc in n_sgg:
            sgg_map[oc] = oc          # 코드 존치(명칭 표기만 다름: 예 36110 세종)
        else:
            sgg_unmatched.append(f'{oc} {on} (후보 {len(cand)}개 · 분구/재편 추정)')

    # ---------------------------------------------------------- 3. 행정동
    n_adm_by_sgg = collections.defaultdict(lambda: collections.defaultdict(list))
    n_adm_by_sido = collections.defaultdict(lambda: collections.defaultdict(list))
    for c, n in n_adm.items():
        n_adm_by_sgg[c[:5]][norm(n)].append(c)
        n_adm_by_sido[c[:2]][norm(n)].append(c)

    adm_map, adm_unmatched, offices, office_unmatched = {}, [], {}, []
    manual_used, manual_stale = [], []
    identity = 0
    stat = collections.Counter()

    for oc, (oname, ofull) in sorted(o_adm.items()):
        sido_old = oc[:2]
        stat[f'total:{sido_old}'] += 1

        # 3-1. 출장소: 행정동 하위 단위 → 코드 변환 대상 아님
        if oname.endswith('출장소'):
            # 소속 행정동 판정: 출장소명의 접두사와 일치하는 실재 행정동명 중 '가장 긴' 것.
            # ('낙동면동부출장소' → '낙동' 이 아니라 '낙동면')
            key = norm(oname)
            parent_sgg = sgg_map.get(oc[:5]) or (oc[:5] if oc[:5] in {c[:5] for c in n_adm} else None)
            pool = n_adm_by_sgg[parent_sgg] if parent_sgg else n_adm_by_sido.get(sido_map.get(sido_old), {})
            hits = [(nm, cs) for nm, cs in pool.items() if key.startswith(nm) and len(cs) == 1]
            hits.sort(key=lambda x: -len(x[0]))
            if hits and (len(hits) == 1 or len(hits[0][0]) > len(hits[1][0])):
                offices[oc] = hits[0][1][0]
            else:
                office_unmatched.append(f'{oc} {ofull}')
            stat['office'] += 1
            continue

        # 3-2. 코드가 그대로 살아있으면 변환 불필요
        if oc in n_adm:
            identity += 1
            stat[f'identity:{sido_old}'] += 1
            continue

        # 3-2b. 개칭 수기 등재분 — 명칭이 통째로 바뀌어 자동 매칭이 불가능한 건
        if oc in MANUAL_ADM:
            tgt, change, basis = MANUAL_ADM[oc]
            if tgt in n_adm:
                adm_map[oc] = tgt
                manual_used.append({'old': oc, 'new': tgt, 'change': change, 'basis': basis})
                stat[f'mapped:{sido_old}'] += 1
                stat['scope:manual'] += 1
                continue
            # 대상 신코드가 사라졌다 = 그 사이 또 개편됐다는 뜻. 조용히 넘기면 안 된다.
            manual_stale.append(f'{oc}→{tgt} ({change}) 대상 신코드가 code_map 에 없음')
            log(f'[warn] MANUAL_ADM 항목이 낡음: {oc}→{tgt} ({change}) — 신코드 부재')

        # 3-3. 시군구 매핑이 성립하면 그 안에서 동명 유일 매칭
        cand = []
        scope = None
        ps = sgg_map.get(oc[:5])
        if ps:
            cand = n_adm_by_sgg[ps].get(norm(oname), [])
            scope = 'sgg'
        # 3-4. 시군구가 분구/재편되어 매핑 불가면 시도 범위에서 동명 유일 매칭
        if len(cand) != 1:
            ns = sido_map.get(sido_old)
            if ns:
                c2 = n_adm_by_sido[ns].get(norm(oname), [])
                if len(c2) == 1:
                    cand, scope = c2, 'sido'
                elif len(cand) != 1:
                    cand, scope = c2, 'sido'
        if len(cand) == 1:
            adm_map[oc] = cand[0]
            stat[f'mapped:{sido_old}'] += 1
            stat[f'scope:{scope}'] += 1
        else:
            adm_unmatched.append(f'{oc} {ofull} (후보 {len(cand)}개)')
            stat[f'unmatched:{sido_old}'] += 1

    # 신코드 쪽 미사용(=구체계에 대응 없음) 점검
    used_new = set(adm_map.values()) | (set(o_adm) & set(n_adm))
    new_orphan = sorted(c for c in n_adm if c not in used_new)

    # ---------------------------------------------------------- 통계
    def rate(pfx):
        tot = stat[f'total:{pfx}']
        if not tot:
            return None
        ident = stat[f'identity:{pfx}']
        mp = stat[f'mapped:{pfx}']
        un = stat[f'unmatched:{pfx}']
        return {'total': tot, 'identity': ident, 'mapped': mp, 'unmatched': un,
                'resolved_ratio': round((ident + mp) / tot, 4)}

    per_sido = {k: rate(k) for k in sorted({c[:2] for c in o_adm}) if rate(k)}
    tot_all = len(o_adm)
    resolved = identity + len(adm_map)
    overall = {'old_adm_total': tot_all, 'identity': identity, 'mapped': len(adm_map),
               'branch_office': stat['office'], 'unmatched': len(adm_unmatched),
               'resolved_ratio_excl_office': round(resolved / (tot_all - stat['office']), 4),
               'resolved_ratio_incl_office': round(resolved / tot_all, 4)}

    out = {
        'meta': {
            'note': '구체계(29 광주/46 전남, 개편 전 인천 등) → 현행 코드 변환. '
                    '2026-07-01 전남광주통합특별시(12) 출범 및 인천 자치구 재편 반영.',
            'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'direction': 'old → new (단방향)',
            'method': '레벨별 명칭 유일 매칭. 시도는 명칭 동일 → 그 외는 하위 시군구 명칭 중복률(≥0.8)로 판정. '
                      '시군구는 매핑된 시도 내 명칭 유일 매칭. 행정동은 (1)코드 동일 (2)매핑된 시군구 내 명칭 유일 '
                      '(3)시군구가 분구·재편된 경우 시도 내 명칭 유일 순으로 매칭. '
                      '후보가 0개이거나 2개 이상이면 추측하지 않고 unmatched 로 분리.',
            'usage': 'new_code = map["adm"].get(old_code, old_code) — 매핑에 없으면 코드가 그대로 유효하거나 '
                     'unmatched 이며, unmatched 는 조인 실패로 드러나게 두는 것이 의도된 동작.',
            'left_source': os.path.relpath(args.pop, ROOT).replace('\\', '/'),
            'right_source': 'data/processed/code_map.json',
            'name_normalization': "공백 제거, '.'·'・'·'ㆍ' → '·', '제N' → 'N'",
            'incheon_note': '인천(28) 변경은 전남광주통합과 무관한 자치구 재편(중구→제물포구·영종구, '
                            '서구→서해구·검단구). 성격이 다르지만 동일한 명칭 매칭으로 해결되어 adm 에 함께 수록.',
            'branch_office_note': "'출장소'는 행정동 하위 단위이며 경계 데이터에 독립 행정동으로 존재하지 않는다. "
                                  'adm 매핑이 아니라 branch_office(출장소 구코드 → 소속 행정동 신코드)로 분리했다. '
                                  '팀원2는 인구를 소속 행정동에 합산해야 이중집계/누락이 없다.',
            'stats': overall,
            'stats_by_old_sido': per_sido,
            'scope_breakdown': {'by_sgg': stat['scope:sgg'], 'by_sido_fallback': stat['scope:sido'],
                                'by_manual': stat['scope:manual']},
            'manual_overrides': manual_used,
            'manual_overrides_note': '개칭(행정동 명칭 변경)은 좌우 명칭에 공통 문자열이 없어 자동 매칭이 '
                                     '원리적으로 불가능하다. 지자체 고시로 확인된 건만 scripts/geo_legacy_map.py 의 '
                                     'MANUAL_ADM 에 등재한다. 관할구역 조정이 동반된 경우는 인구 1:1 이관이 '
                                     '틀린 숫자를 만들므로 등재하지 않고 unmatched 로 둔다.',
            'manual_overrides_stale': manual_stale,
            'unmatched_count': len(adm_unmatched),
            'sgg_not_applicable': sgg_na,
            'sgg_not_applicable_note': '통합시의 시 레벨 행(예: 41110 수원시)·출장소 행 등 하위 행정동이 없는 '
                                       '집계용 행. 현행 sgg 체계에 대응 개념이 없어 매핑 대상이 아니다.',
            'evidence': evidence,
        },
        'sido': {k: v for k, v in sorted(sido_map.items()) if k != v},
        'sgg': {k: v for k, v in sorted(sgg_map.items()) if k != v},
        'adm': dict(sorted(adm_map.items())),
        'branch_office': dict(sorted(offices.items())),
        'unmatched': {
            'sido': sorted(f'{c} {n}' for c, n in o_sido.items() if c not in sido_map),
            'sgg': sgg_unmatched,
            'adm': adm_unmatched,
            'branch_office': office_unmatched,
            'new_without_old': [f'{c} {n_sgg.get(c[:5], "")} {n_adm[c]}' for c in new_orphan],
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    log('---- 결과 ----')
    log('sido 변경:', out['sido'])
    log('sgg 변경 건수:', len(out['sgg']))
    log('adm 매핑:', len(out['adm']), '/ identity:', identity, '/ 출장소:', stat['office'],
        '/ unmatched:', len(adm_unmatched))
    log('전체 해소율(출장소 제외):', overall['resolved_ratio_excl_office'])
    for k, v in per_sido.items():
        if v['mapped'] or v['unmatched']:
            log(f'  시도 {k}: total={v["total"]} identity={v["identity"]} mapped={v["mapped"]} '
                f'unmatched={v["unmatched"]} 해소율={v["resolved_ratio"]:.2%}')
    log('신코드 중 구체계 대응 없음:', len(new_orphan))
    log('저장:', OUT, f'{os.path.getsize(OUT):,} bytes')


if __name__ == '__main__':
    main()
