#!/usr/bin/env bash
# 发票报销台 · 一键安装（macOS / Linux）
# 用法: bash install.sh   装依赖 + 建投递夹 + 打印自启注册命令（不自自动写系统）
set -e
TOOL="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$TOOL/start.sh" 2>/dev/null || true

echo "[1/3] 安装 Python 依赖 (flask openpyxl pillow pymupdf mcp)..."
PY="${PYTHON:-python3}"
"$PY" -m pip install -q --user flask openpyxl pillow pymupdf mcp \
  || "$PY" -m pip install -q flask openpyxl pillow pymupdf mcp

echo "[2/3] 建立本机投递夹 $HOME/发票投递 ..."
mkdir -p "$HOME/发票投递"

echo "[3/3] 开机自启（按需执行，以下为命令，未自动写入系统以防意外）..."
case "$(uname)" in
  Darwin)
    echo "  macOS 自启："
    echo "    launchctl load \"$TOOL/com.invoiceorganizer.server.plist\""
    echo "  （请先把该 plist 中 __TOOL__ 替换为: $TOOL）"
    ;;
  Linux)
    echo "  Linux 自启："
    echo "    systemctl --user enable --now \"$TOOL/invoice-organizer.service\""
    ;;
esac

echo
echo "完成。启动网页：  bash $TOOL/start.sh"
echo "手动投递文件：    bash $TOOL/watch_drop.py --once"
