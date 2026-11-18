@echo off
setlocal
cd /d "%~dp0.." || exit /b 1
".venv\Scripts\python.exe" -m agent.chrome_native_host "%~1"
