@echo off
:: ============================================================
::  SS Workspace - Auto Startup Script
::  Runs automatically on Windows login via Task Scheduler.
::  No dependency on any external launcher or app.
:: ============================================================

set APP_DIR=C:\Users\12019\.gemini\antigravity\scratch\telegram-cloud-drive
set PYTHON=%APP_DIR%\venv\Scripts\python.exe
set LOG_DIR=%APP_DIR%\logs
set LOG_FILE=%LOG_DIR%\server.log

:: Create logs directory if missing
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Move to app directory
cd /d "%APP_DIR%"

:: Bounded log rotation: if server.log exceeds 10MB, rotate to .old to prevent disk exhaustion DoS
if exist "%LOG_FILE%" (
    for %%I in ("%LOG_FILE%") do (
        if %%~zI gtr 10485760 (
            del /f /q "%LOG_FILE%.old" >nul 2>&1
            move /y "%LOG_FILE%" "%LOG_FILE%.old" >nul 2>&1
        )
    )
)

:: Wait 3 seconds for network interfaces to stabilize on login
ping 127.0.0.1 -n 4 >nul

:: ----------------------------------------------------------
:: Launch Uvicorn backend bound strictly to localhost (127.0.0.1)
:: with --no-access-log to prevent disk exhaustion
:: ----------------------------------------------------------
echo [%DATE% %TIME%] Starting SS Workspace backend... >> "%LOG_FILE%"
set PYTHONUNBUFFERED=1
"%PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log >> "%LOG_FILE%" 2>&1
