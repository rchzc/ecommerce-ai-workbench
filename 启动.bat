@echo off
chcp 65001 >nul
title 跨境电商 AI 运营工作台
cd /d "%~dp0"

echo.
echo  ============================================
echo   跨境电商 AI 运营工作台 - 一键启动
echo  ============================================
echo.

REM ---- 1. 检查虚拟环境 ----
if not exist "backend\.venv\Scripts\activate.bat" (
    echo  [错误] 未找到虚拟环境 backend\.venv
    echo  请先执行：cd backend ^&^& python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM ---- 2. 检查 .env 与 API Key ----
if not exist "backend\.env" (
    echo  [提示] 未找到 backend\.env，正在从模板创建...
    copy "backend\.env.example" "backend\.env" >nul
    echo  [警告] 请编辑 backend\.env 填入你的 LLM_API_KEY 后重新运行本脚本
    echo.
    notepad "backend\.env"
    pause
    exit /b 1
)

findstr /r /c:"^LLM_API_KEY=..*" "backend\.env" >nul
if errorlevel 1 (
    echo  [警告] backend\.env 中 LLM_API_KEY 为空，智能体将无法调用模型
    echo  请填入阿里云百炼 API Key 后重新运行
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0backend"
call .venv\Scripts\activate.bat

echo  [1/2] 启动后端服务...
echo        地址 http://127.0.0.1:8000
echo        接口文档 http://127.0.0.1:8000/api/docs
echo.
echo  [2/2] 正在打开浏览器...
echo        （若未自动打开，请手动访问 http://127.0.0.1:8000）
echo.
echo  ============================================
echo   保持本窗口开启；关闭窗口即停止服务
echo  ============================================
echo.

REM 延迟 2 秒等后端起来再开浏览器
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"

uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo  服务已停止
pause
