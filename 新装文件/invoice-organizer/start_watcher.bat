@echo off
REM 发票整理器 · 投递监视器启动器（每台电脑双击一次即可常驻）
setlocal
set "TOOL=%~dp0"
set "PY="
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
  set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
if "%PY%"=="" set "PY=python"
start "发票投递监视器" "%PY%" "%TOOL%watch_drop.py"
echo 投递监视器已启动（新窗口）。把发票拖进 %USERPROFILE%\发票投递 即可自动入队。
pause
