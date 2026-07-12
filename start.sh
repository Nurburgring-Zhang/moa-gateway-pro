#!/usr/bin/env bash
# MoA Gateway Pro - Linux/macOS 启动脚本
# 功能:
#   1. 检查 Python
#   2. 调用 start.py,自动创建 venv + 自动装依赖 + 自动启 watchdog
#   3. 故障时 watchdog 自动重启子进程
#   4. 收到 SIGINT/SIGTERM 时,watchdog + 全部子进程一起退出
set -e
cd "$(dirname "$0")"

echo "=== MoA Gateway Pro - Unix 启动器 ==="
echo

# 1) Python 检查
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未找到 python3,请先安装 Python 3.10+"
    exit 1
fi

# 2) 启动
exec python3 start.py serve
