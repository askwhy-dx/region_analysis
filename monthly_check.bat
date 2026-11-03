@echo off
REM ==========================================================================
REM  monthly_check.bat - monthly checkup (E-3), first weekday of each month
REM
REM  The check itself is read-only. Right after it, scripts\auto_update.py
REM  refreshes each data set whose check passed and holds the rest (2026-11-03):
REM    monthly  : competitor        quarterly (Jan/Apr/Jul/Oct): population,
REM    apartment trades, K-apt households, childcare, academy -> verify -> publish
REM  One Slack message reports updated / held / pending.
REM  On other weekdays it runs auto_update.py --resume (K-apt takes ~3 days).
REM  The scheduler calls this DAILY; monthly_check.py --gate decides whether
REM  today actually runs (day 1-5, weekday, not a holiday, not already done).
REM  schtasks has no "first weekday" trigger, hence the split.
REM
REM  !! Do NOT add `chcp` here.
REM     chcp changes the code page for the WHOLE console and it is not
REM     restored on exit - it once garbled an entire running session.
REM     This file is ASCII on purpose; Korean output comes from python,
REM     which follows whatever encoding the console already uses.
REM
REM  Manual run:  monthly_check.bat force       (check + auto update)
REM               monthly_check.bat checkonly   (check only, Slack from check)
REM ==========================================================================
setlocal
cd /d "%~dp0"

set PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

if /i "%~1"=="force" goto :run
if /i "%~1"=="checkonly" goto :checkonly

"%PY%" scripts\monthly_check.py --gate
if errorlevel 1 goto :skip

:run
echo.
"%PY%" scripts\monthly_check.py --no-slack
echo.
"%PY%" scripts\auto_update.py
set RC=%ERRORLEVEL%
echo.
if %RC%==0 goto :done
echo   [WARN] some data held - see data\logs
endlocal
exit /b 1

:checkonly
"%PY%" scripts\monthly_check.py
endlocal
exit /b %ERRORLEVEL%

:done
echo   done - log in data\logs
endlocal
exit /b 0

:skip
echo   not a checkup day - resume pending K-apt only
"%PY%" scripts\auto_update.py --resume
endlocal
exit /b 0
