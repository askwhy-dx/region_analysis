@echo off
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

set PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

echo.
echo  ============================================
echo   아파트 세대수 갱신 - 수집부터 지표까지
echo  ============================================
echo   전국 22,257단지 / 포털 일일한도 10,000건
echo   한도에 걸리면 다음 날 다시 실행하면 이어받습니다.
echo.

echo  [1/5] 단지별 세대수 수집
"%PY%" scripts\collect_kapt.py
if errorlevel 1 goto :fail_collect

echo.
echo  [2/5] 행정동 집계
"%PY%" scripts\build_kapt.py
if errorlevel 1 goto :not_yet

echo.
echo  [3/5] POI 재조립 (지도 팝업 세대수)
"%PY%" scripts\build_poi.py
if errorlevel 1 goto :fail_step

echo.
echo  [4/5] 지표 재계산 (소비력 2,3 - 아파트 거주 비율/주거자산 총액)
"%PY%" scripts\build_metrics.py
if errorlevel 1 goto :fail_step

echo.
echo  ============================================
echo   완료. 로컬 산출물 갱신됨
echo  ============================================

echo.
echo  [5/5] 검증 + 발행
call publish.bat
if errorlevel 1 goto :fail_publish
goto :end

:fail_publish
echo.
echo  [중단] 검증 실패로 발행하지 않았습니다.
echo         로컬 산출물은 갱신됐지만 공개본은 직전 상태입니다.
echo         원인을 고친 뒤  publish.bat  을 실행하세요.
goto :end

:not_yet
echo.
echo  ------------------------------------------------
echo   아직 수집이 다 끝나지 않았습니다 - 정상입니다.
echo   다음 날 배치를 다시 실행하면 이어받습니다.
echo   그때까지 대시보드는 "수집 중 NN%%" 로 표시됩니다.
echo   ^(집계가 거부되므로 3,4단계도 건너뜁니다^)
echo  ------------------------------------------------
goto :end

:fail_step
echo.
echo  [중단] 재조립/지표 계산에서 실패했습니다.
echo         수집분은 그대로 남아 있으니 원인을 고친 뒤
echo         이 배치를 다시 실행하면 됩니다.
goto :end

:fail_collect
echo.
echo  [중단] 수집에 실패했습니다.
echo         .env 의 DATA_GO_KR_KEY 를 확인하세요.
echo         검증:  "%PY%" scripts\check_datago_key.py
goto :end

:end
if defined OLDCP chcp %OLDCP% > nul
echo.
pause
