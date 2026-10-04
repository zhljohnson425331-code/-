@echo off
REM 发票整理器 · 网页服务启动器（查看/导出/打印用，端口 8731）
setlocal
set "TOOL=%~dp0"
set "PY="
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
  set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
if "%PY%"=="" set "PY=python"
REM 确保依赖（仅首次需要）
"%PY%" -c "import flask,openpyxl" 2>nul || "%PY%" -m pip install -q flask openpyxl pillow pymupdf
echo 启动发票整理器网页： http://127.0.0.1:8731
"%PY%" "%TOOL%server.py"
pause
