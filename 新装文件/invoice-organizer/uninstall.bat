@echo off
REM 发票报销台 · 卸载开机自启（保留数据与工具，仅移除计划任务）
setlocal
schtasks /Delete /TN "发票报销台" /F 2>nul
if errorlevel 1 (
  echo 未找到自启任务（可能未安装或已移除）。
) else (
  echo 已卸载开机自启。工具与数据均保留，需要时双击 start_server.bat 手动启动。
)
pause
