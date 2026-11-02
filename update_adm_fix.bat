@echo off
REM ==========================================================================
REM  update_adm_fix.bat - E-4 행정동 배정 교정 + 하위 산출물 전량 재생성
REM
REM  경계 폴리곤(admdongkor)이 최근 분동을 못 따라가면 POI 가 옆 동에 쌓인다.
REM  JUSO(행안부 도로명주소)를 심판으로 세워 배정을 바로잡고, 그 캐시를 읽는
REM  산출물을 순서대로 다시 만든다.
REM
REM  [주의] 순서가 곧 의존성이다. 중간부터 돌리면 낡은 값이 섞인 채 끝난다.
REM     학원·아파트는 지오코딩 캐시를 다시 읽어야 교정이 반영되고,
REM     학교는 교정 이력에서 '낡은 동' 목록을 받아 가므로 심판 뒤에 와야 한다.
REM
REM  [주의] 기준이 바뀌므로 돌리기 전에 산출물을 archive/ 로 백업할 것.
REM ==========================================================================
setlocal
cd /d "%~dp0"

REM  [주의] chcp 는 이 배치만이 아니라 **콘솔 전역**을 바꾸고, 배치가 끝나도
REM         돌아오지 않는다. UTF-8 터미널에서 돌리면 그 세션의 한글 출력이
REM         전부 깨진다(2026-08-11 실사고). 들어올 때 저장하고 **모든 종료
REM         경로**에서 되돌린다. 한글 echo 는 949 상태에서 나가야 하므로
REM         복원 줄은 echo 들 뒤·pause 앞에 둔다.
set "OLDCP="
for /f "tokens=2 delims=:" %%i in ('chcp') do set "OLDCP=%%i"
set "OLDCP=%OLDCP: =%"
chcp 949 > nul

echo [1/8] 행정동 배정 심판 - JUSO
python scripts\arbitrate_adm.py --scope suspect || goto :fail

echo [2/8] 학원 재집계
python scripts\collect_neis.py --no-download || goto :fail

echo [3/8] 아파트 실거래 재집계
python scripts\collect_rtms.py --no-download || goto :fail

echo [4/8] K-apt 세대수 재집계
python scripts\build_kapt.py --no-geocode || goto :fail

echo [5/8] 학교 재적재 - E-4 재판정 포함
python scripts\load_school.py || goto :fail

echo [6/8] 경쟁사 재조립
python scripts\build_competitor.py --no-geocode || goto :fail

echo [7/8] POI + 지표 재계산
python scripts\build_poi.py || goto :fail
python scripts\build_metrics.py || goto :fail

echo.
echo [8/8] 검증 + 발행
call publish.bat
if errorlevel 1 goto :fail_publish
if defined OLDCP chcp %OLDCP% > nul
goto :eof

:fail_publish
echo.
echo [중단] 검증 실패로 발행하지 않았습니다.
echo        로컬 산출물은 갱신됐지만 공개본은 직전 상태입니다.
if defined OLDCP chcp %OLDCP% > nul
exit /b 1

:fail
echo.
echo [중단] 앞 단계가 실패했다. 산출물이 섞였을 수 있으니 archive 에서 되돌린 뒤 다시 시작할 것.
if defined OLDCP chcp %OLDCP% > nul
exit /b 1
