@echo off
REM ==========================================================================
REM  publish.bat - 검증 -> 발행. 갱신 배치들이 끝에서 call 한다.
REM
REM  [주의] 발행은 검증을 통과해야만 한다. 공개 URL 이라 틀린 데이터가 올라가면
REM         그대로 서비스되고, 화면은 정상으로 보이며 배지도 "실데이터" 라서
REM         눈으로는 알아챌 수 없다. 그래서 여기서 게이트를 건다.
REM
REM  [주의] pause 를 넣지 않는다. 부르는 배치가 알아서 멈춘다.
REM         exit /b 로 끝내야 부모 배치가 안 죽는다.
REM
REM  단독 실행도 된다:  publish.bat
REM  올릴 것만 보려면 :  python scripts\deploy_pages.py --dry-run
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

set PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

echo.
echo  --- 검증 1/2 - 데이터
"%PY%" scripts\verify_c8.py
if errorlevel 1 goto :fail_data

echo.
echo  --- 검증 2/2 - 화면
node scripts\verify_dashboard.js
if errorlevel 1 goto :fail_web

echo.
echo  --- 발행 - GitHub Pages
"%PY%" scripts\deploy_pages.py
if errorlevel 1 goto :fail_deploy

echo.
echo   발행 완료  https://jichang999-dotcom.github.io/tes2/jiyeok/web/
echo   Pages 반영에 40~60초 걸립니다.
if defined OLDCP chcp %OLDCP% > nul
endlocal
exit /b 0

:fail_data
echo.
echo  [중단] 데이터 검증 FAIL - 발행하지 않았습니다.
echo         공개본은 직전 상태 그대로입니다. 원인을 고친 뒤 publish.bat 재실행.
if defined OLDCP chcp %OLDCP% > nul
endlocal
exit /b 1

:fail_web
echo.
echo  [중단] 화면 검증 FAIL - 발행하지 않았습니다.
echo         공개본은 직전 상태 그대로입니다.
if defined OLDCP chcp %OLDCP% > nul
endlocal
exit /b 1

:fail_deploy
echo.
echo  [중단] 발행 실패. 원격은 바뀌지 않았습니다 - 트리/커밋을 한 번에 올리기 때문입니다.
echo         잠시 뒤 publish.bat 을 다시 실행하세요.
if defined OLDCP chcp %OLDCP% > nul
endlocal
exit /b 1
