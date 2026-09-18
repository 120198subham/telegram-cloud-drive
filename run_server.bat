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

:: Wait 3 seconds for network interfaces to stabilize on login
timeout /t 3 /nobreak >nul

:: ----------------------------------------------------------
:: Launch Uvicorn backend (stdout + stderr sent to log file)
:: ----------------------------------------------------------
echo [%DATE% %TIME%] Starting SS Workspace backend... >> "%LOG_FILE%"
"%PYTHON%" -m uvicorn main:app --host 0.0.0.0 --port 8000 >> "%LOG_FILE%" 2>&1



