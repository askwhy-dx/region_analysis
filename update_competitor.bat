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
echo   자사 · 경쟁사 · 시너지세권 위치 월간 갱신
echo  ============================================
echo.
echo  [1/6] 브랜드 홈페이지 크롤링 - 경쟁사 4개 + 시너지세권 2개
"%PY%" scripts\crawl_competitor.py --delay 0.5
if errorlevel 2 goto :fail_crawl
if errorlevel 1 echo   ! 일부 브랜드 실패 - 직전 회차 데이터가 유지됩니다

echo.
echo  [2/6] 자사 와이키즈 xlsx 로드
"%PY%" scripts\load_competitor.py --preset waikids
if errorlevel 1 echo   ! 와이키즈 xlsx 로드 실패 - 직전 회차 데이터가 유지됩니다

echo.
echo  [3/6] 자사 와이즈만 xlsx 로드
"%PY%" scripts\load_competitor.py --preset wiseman
if errorlevel 1 echo   ! 와이즈만 xlsx 로드 실패 - 직전 회차 데이터가 유지됩니다

echo.
echo  [4/6] 지오코딩 + 행정동 부여 + 집계
"%PY%" scripts\build_competitor.py
if errorlevel 1 goto :fail_build

echo.
echo  [5/6] POI 재조립
"%PY%" scripts\build_poi.py
if errorlevel 1 goto :fail_poi

echo.
echo  ============================================
echo   완료. 로컬 산출물 갱신됨
echo  ============================================

echo.
echo  [6/6] 검증 + 발행
call publish.bat
if errorlevel 1 goto :fail_publish
echo.
goto :end

:fail_crawl
echo.
echo  [중단] 크롤링이 한 건도 성공하지 못했습니다.
echo         사이트 개편 또는 네트워크 문제입니다.
echo         data\raw\competitor\_snapshot 의 원문을 확인하세요.
goto :end

:fail_build
echo.
echo  [중단] 하드가드 위반입니다.
echo         지점수 급감 또는 지오코딩 실패율 초과입니다.
echo         원인 확인 후 의도한 감소라면 --no-guard 로 재실행하세요.
goto :end

:fail_poi
echo.
echo  [중단] POI 조립 실패입니다.
goto :end

:fail_publish
echo.
echo  [중단] 검증 실패로 발행하지 않았습니다.
echo         로컬 산출물은 갱신됐지만 공개본은 직전 상태입니다.
echo         원인을 고친 뒤  publish.bat  을 실행하세요.
goto :end

:end
if defined OLDCP chcp %OLDCP% > nul
endlocal
pause
