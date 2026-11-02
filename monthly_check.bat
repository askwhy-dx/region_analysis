@echo off
REM ==========================================================================
REM  monthly_check.bat - monthly checkup (E-3), first weekday of each month
REM
REM  Read-only. It never edits data - it reports whether a refresh is due.
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
REM  Manual run:  monthly_check.bat force
REM ==========================================================================
setlocal
cd /d "%~dp0"

set PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

if /i "%~1"=="force" goto :run

"%PY%" scripts\monthly_check.py --gate
if errorlevel 1 goto :skip

:run
echo.
"%PY%" scripts\monthly_check.py
set RC=%ERRORLEVEL%
echo.
if %RC%==0 goto :done
echo   [WARN] checkup reported failures - see data\logs
endlocal
exit /b 1

:done
echo   done - log in data\logs
endlocal
exit /b 0

:skip
echo   not a checkup day - skipped
endlocal
exit /b 0
