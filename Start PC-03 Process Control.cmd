@echo off
setlocal
cd /d "%~dp0"
set "CLOUDNET_CA=secrets\server.crt"
if not exist "%CLOUDNET_CA%" set "CLOUDNET_CA=server.crt"
set "CLOUDNET_TOKEN=secrets\agent-token.txt"
if not exist "%CLOUDNET_TOKEN%" set "CLOUDNET_TOKEN=secrets\token.txt"
if not exist ".venv\Scripts\python.exe" goto missing
if not exist "%CLOUDNET_CA%" goto missing
if not exist "%CLOUDNET_TOKEN%" goto missing
echo PC-03 only. Stop your old Agent with Ctrl+C before continuing.
echo Process inventory and protected termination will be enabled.
echo Remote exam control will be permitted. Exam mode starts OFF.
pause
".venv\Scripts\python.exe" -m agent.main --host 13.238.97.144 --port 5000 --pc-id PC-03 --ca-file "%CLOUDNET_CA%" --token-file "%CLOUDNET_TOKEN%" --server-name 32.236.13.91 --live --enable-process-control --allow-remote-exam-control
echo Agent stopped. Review the messages above.
pause
exit /b
:missing
echo Missing Python environment, certificate, or this PC's Agent token.
echo Run this file from the CloudNet project folder with existing credentials.
pause
exit /b 1
