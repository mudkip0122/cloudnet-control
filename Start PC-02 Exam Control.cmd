@echo off
setlocal
cd /d "%~dp0"
set "CLOUDNET_CAMERA="
if /i "%~1"=="--share-exam-camera" set "CLOUDNET_CAMERA=--share-exam-camera"
set "CLOUDNET_CA=secrets\server.crt"
if not exist "%CLOUDNET_CA%" set "CLOUDNET_CA=server.crt"
set "CLOUDNET_TOKEN=secrets\agent-token.txt"
if not exist "%CLOUDNET_TOKEN%" set "CLOUDNET_TOKEN=secrets\token.txt"
if not exist ".venv\Scripts\python.exe" goto missing
if not exist "%CLOUDNET_CA%" goto missing
if not exist "%CLOUDNET_TOKEN%" goto missing
echo PC-02: process inventory and protected termination will be enabled.
echo Remote exam control will be permitted. Exam mode starts OFF.
echo Chrome exam tab monitoring is available during an exam.
if defined CLOUDNET_CAMERA echo Exam camera sharing is enabled for compatible servers.
echo Stop any other PC-02 Agent before running this file.
pause
".venv\Scripts\python.exe" -m agent.main --host 13.238.97.144 --port 5000 --ca-file "%CLOUDNET_CA%" --token-file "%CLOUDNET_TOKEN%" --server-name 32.236.13.91 --pc-id PC-02 --live --enable-process-control --allow-remote-exam-control --watch-chrome-tabs %CLOUDNET_CAMERA% --log-file runtime/logs/pc02-exam.log
echo Agent stopped. Check the messages above.
pause
exit /b
:missing
echo Missing existing Python environment, certificate or PC-02 token.
pause
exit /b 1
