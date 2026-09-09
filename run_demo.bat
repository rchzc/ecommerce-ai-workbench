@echo off
chcp 65001 >nul
title Step2 - 跑通 AI 那一半
cd /d "%~dp0"

set "PY=%~dp0backend\.venv\Scripts\python.exe"
set "SCRIPT=%~dp0examples\feishu-bitable\bitable_sync.py"

if not exist "%PY%" (
    echo [ERROR] 找不到虚拟环境：backend\.venv\Scripts\python.exe
    echo         请先确认项目完整。
    pause
    exit /b 1
)

echo.
echo [0/2] 检查工作台是否已启动...
curl -s -m 3 http://127.0.0.1:8000/api/health >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 工作台没启动，或没启动完。
    echo         请先双击  启动.bat  ，等窗口出现
    echo         Uvicorn running on http://127.0.0.1:8000
    echo         再回来双击本文件。
    echo.
    pause
    exit /b 1
)
echo       工作台已就绪。

echo.
echo [1/2] 调用补货 Agent（内置 3 条样例 SKU，不碰飞书）...
echo.
set WORKFLOW_API_KEY=dev-local-key-2026
"%PY%" "%SCRIPT%" --demo

echo.
echo [2/2] 完成。
echo       看到「成功 2 行｜失败 0 行」就是通了 —— 请截图保存。
echo.
pause
