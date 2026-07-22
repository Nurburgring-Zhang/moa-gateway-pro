@echo off
REM MoA Gateway Pro - Windows 启动脚本
REM 功能:
REM   1. 检查 Python
REM   2. 调用 start.py,自动创建 venv + 自动装依赖 + 自动启 watchdog
REM   3. 故障时 watchdog 自动重启子进程
REM   4. 关闭窗口时,watchdog + 全部子进程一起退出
chcp 65001 > nul
setlocal EnableExtensions

cd /d "%~dp0"

echo === MoA Gateway Pro - Windows 启动器 ===
echo.

REM 1) Python 检查
where python > nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python,请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

REM 2) 启动(start.py 自己会处理 venv/依赖/watchdog)
python start.py serve
endlocal
