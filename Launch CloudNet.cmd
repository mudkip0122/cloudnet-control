@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python environment is missing. Create .venv first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "import boto3, psutil" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements-launcher.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check your internet connection.
        pause
        exit /b 1
    )
)
start "" ".venv\Scripts\pythonw.exe" -m launcher.main
