#!/usr/bin/env bash
# 发票报销台 · 静默启动器（macOS / Linux）
# 用法: bash start.sh   后台启动网页服务，日志写到同目录 server.log
set -e
TOOL="$(cd "$(dirname "$0")" && pwd)"
PY=""
# 优先用 WorkBuddy 自带的 managed python（免装依赖），否则回退系统 python3
if [ -x "$HOME/.workbuddy/binaries/python/versions/3.13.12/python.exe" ]; then
  PY="$HOME/.workbuddy/binaries/python/versions/3.13.12/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  PY="python"
fi
cd "$TOOL"
nohup "$PY" server.py >> "$TOOL/server.log" 2>&1 &
echo "已后台启动发票报销台 (pid $!), 日志: $TOOL/server.log"
echo "浏览器访问: http://127.0.0.1:${INVOICE_PORT:-8731}"
