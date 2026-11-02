@echo off
REM ==========================================================================
REM  update_childcare.bat - daily catch-up for the childcare axis
REM
REM  The scheduler calls this DAILY. All logic lives in python:
REM    - nothing to do  -> exits quietly
REM    - quota not back -> exits quietly, retries tomorrow
REM    - all filled     -> verifies (2 gates) and publishes
REM
REM  !! Do NOT add `chcp` here - same reason as monthly_check.bat: it changes
REM     the code page for the WHOLE console and is not restored on exit.
REM     This file is ASCII on purpose; Korean output comes from python.
REM  !! Do NOT add `pause` - this runs unattended.
REM
REM  Manual run:  update_childcare.bat
REM  Log:         data\logs\childcare_YYYYMMDD.log
REM ==========================================================================
setlocal
cd /d "%~dp0"

set PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

"%PY%" scripts\childcare_daily.py
set RC=%ERRORLEVEL%

endlocal & exit /b %RC%
