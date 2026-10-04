@echo off
REM 发票报销台 · 静默启动器（供开机自启 / 计划任务调用，无 pause、输出写日志）
setlocal
set "TOOL=%~dp0"
set "PY="
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
  set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
if "%PY%"=="" set "PY=python"
"%PY%" "%TOOL%server.py" >> "%TOOL%server.log" 2>&1
