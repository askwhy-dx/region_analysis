# 대시보드 (web/) — 실행 안내

담당: 팀원4 · 정적 HTML + Leaflet(로컬 vendor). 빌드 툴 없음.

## 1. 실행 방법 (권장)

```
web\serve.bat   더블클릭
```
- 프로젝트 **루트**를 `python -m http.server 8765` 로 서비스하고 브라우저를 엽니다.
- 주소: <http://localhost:8765/web/>
- 루트를 서비스해야 `../data/geo`, `../data/processed` 의 실데이터를 읽을 수 있습니다.
  (`web/` 만 서비스하면 상위 폴더 접근이 404 → 전부 mock 으로 폴백됩니다.)

## 2. `file://` 로 그냥 열면?

`index.html` 을 더블클릭해도 화면은 뜨지만, 브라우저 CORS 정책 때문에 JSON fetch 가
막혀 "데이터 준비중" 안내가 나올 수 있습니다. → **serve.bat 을 쓰세요.**

## 3. 데이터 우선순위

| 순서 | 경로 | 설명 |
|---|---|---|
| 1 | `../data/geo/`, `../data/processed/` | 팀 실데이터 (있으면 항상 우선) |
| 2 | `web/mock/` | 팀원4가 만든 개발용 mock (스키마는 DATA_CONTRACT 동일) |

- mock 이 한 개라도 쓰이면 헤더 우측 배지가 **"⚠ 샘플(mock) 데이터"** 로 바뀝니다.
- 둘 다 없으면 빈 화면 대신 **"데이터 준비중"** 안내를 띄웁니다.

## 4. 화면 구조

```
[전국] sido.geojson + metrics_sido.json
  → 시도 클릭 → [시도] sgg.geojson(해당 시도 필터) + metrics_sgg.json
  → 시군구 클릭 → [시군구] adm_<sgg_cd>.geojson (이 시점에만 지연 로딩) + metrics.json
  → 동 클릭 → [동 상세]  좌: 지표 4블록(시장규모/경쟁강도/구매력/접근성)
                          우: POI 맵(학교·학원·아파트 레이어 토글)
```
- 상단 브레드크럼으로 상위 단계 복귀.
- 행정동 경계는 **전국 일괄 로딩하지 않습니다** (시군구 클릭 시 해당 파일만).

## 5. 인쇄 (A4)

`Ctrl+P` → A4 세로. 지도·지표 패널이 페이지 안에 들어가도록 print CSS 적용,
지도 컨트롤/토글 버튼은 인쇄에서 숨김. 인쇄 직전 Leaflet 을 강제 리사이즈합니다.

## 6. 폴더

```
web/
├─ index.html        대시보드 본체 (단일 진입점, CSS/JS 인라인)
├─ serve.bat         로컬 서버 (CRLF + cp949)
├─ vendor/           Leaflet 1.9.4 로컬 사본 (오프라인 동작, CDN 미사용)
│  ├─ leaflet.js  leaflet.css  images/
└─ mock/             개발용 mock 데이터 (실데이터 없을 때만 사용)
   ├─ build_mock.py  mock 생성기 (data/raw/geo 원본 → 경계 dissolve + 통계 더미)
   ├─ geo/           sido.geojson, sgg.geojson, adm_<sgg>.geojson, adm_index.json
   ├─ code_map.json  population*.json  apartment*.json  metrics*.json
   └─ poi_<sgg>.json
```

> 배경 지도 타일(OSM)은 인터넷이 있을 때만 표시되고, 없으면 자동으로 제거됩니다.
> 경계·POI 렌더링은 타일 없이도 정상 동작합니다.

## 7. POI 재생성

```
"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" scripts\collect_poi.py --sample
"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" web\mock\build_mock.py   (mock 갱신 시)
```
