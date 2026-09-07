@echo off
title AI Workbench
cd /d "%~dp0"
set "ROOT=%~dp0"
set "LOG=%ROOT%server_run.log"
echo [%date% %time%] bat started > "%LOG%"

set "PY=%ROOT%backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [%time%] ERROR venv python missing >> "%LOG%"
    echo [ERROR] venv missing: backend\.venv\Scripts\python.exe
    echo Please run: cd backend ^&^& python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)
echo [%time%] venv python found >> "%LOG%"

if not exist "%ROOT%backend\.env" (
    echo [%time%] .env missing, creating from template >> "%LOG%"
    copy "%ROOT%backend\.env.example" "%ROOT%backend\.env" >nul
    echo [WARN] backend\.env created from template. Please set LLM_API_KEY, then re-run.
    notepad "%ROOT%backend\.env"
    pause
    exit /b 1
)

findstr /r /c:"^LLM_API_KEY=..*" "%ROOT%backend\.env" >nul
if errorlevel 1 (
    echo [%time%] LLM_API_KEY empty >> "%LOG%"
    echo [WARN] LLM_API_KEY is empty in backend\.env
    pause
    exit /b 1
)

cd /d "%ROOT%backend"
echo [%time%] starting uvicorn >> "%LOG%"
echo.
echo [1/2] Starting backend: http://127.0.0.1:8000
echo [2/2] Opening browser in 3 seconds...
echo.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"

"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set "RC=%errorlevel%"
echo [%time%] uvicorn exited code %RC% >> "%LOG%"
echo.
echo Server stopped (code %RC%). See server_run.log
echo.
pause
