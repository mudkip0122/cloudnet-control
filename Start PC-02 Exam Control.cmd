@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
if not exist "secrets\server.crt" goto missing
if not exist "secrets\agent-token.txt" goto missing
echo PC-02: remote exam control permitted. Exam mode starts OFF.
echo Stop any other PC-02 Agent before running this file.
".venv\Scripts\python.exe" -m agent.main --host 13.238.97.144 --port 5000 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --server-name 32.236.13.91 --pc-id PC-02 --live --allow-remote-exam-control --log-file runtime/logs/pc02-exam.log
echo Agent stopped. Check the messages above.
pause
exit /b
:missing
echo Missing existing Python environment, certificate or PC-02 token.
pause
exit /b 1
