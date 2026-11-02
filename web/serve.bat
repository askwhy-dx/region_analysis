@echo off
chcp 949 > nul
setlocal
cd /d "%~dp0.."
set PORT=8765
set PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo.
echo  지역분석 대시보드 로컬 서버
echo  ----------------------------------------
echo  주소: http://localhost:%PORT%/web/
echo  종료: 이 창에서 Ctrl+C
echo  (프로젝트 루트를 서비스하므로 data 폴더의 실데이터도 함께 읽습니다)
echo.
start "" http://localhost:%PORT%/web/
"%PY%" -m http.server %PORT%
endlocal
