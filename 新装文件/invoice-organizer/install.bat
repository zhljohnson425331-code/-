@echo off
REM 发票报销台 · 一键安装（每台电脑跑一次）
REM 装依赖 + 建本机投递夹 + 注册开机登录自启（网页服务，端口 8731）
setlocal
set "TOOL=%~dp0"
set "PY="
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
  set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
if "%PY%"=="" set "PY=python"

echo [1/3] 检查 Python 依赖（flask/openpyxl/pillow/pymupdf）…
"%PY%" -c "import flask,openpyxl" 2>nul || "%PY%" -m pip install -q flask openpyxl pillow pymupdf
if errorlevel 1 (
  echo 依赖安装失败，请检查网络或手动执行：pip install flask openpyxl pillow pymupdf
  pause
  exit /b 1
)

echo [2/3] 建立本机投递夹 %USERPROFILE%\发票投递 …
if not exist "%USERPROFILE%\发票投递" mkdir "%USERPROFILE%\发票投递"

echo [3/3] 注册开机登录自启（计划任务：发票报销台）…
schtasks /Create /TN "发票报销台" /TR "\"%TOOL%launch_server.bat\"" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 (
  echo 自启注册失败（可能需要以管理员身份运行），可手动双击 start_server.bat 使用。
) else (
  echo 已注册：下次登录 Windows 自动启动网页服务。
)

echo.
echo 完成。打开浏览器访问 http://127.0.0.1:8731 即可使用报销台。
echo 手动启动：双击 start_server.bat（网页）+ start_watcher.bat（拖拽监视）。
pause
