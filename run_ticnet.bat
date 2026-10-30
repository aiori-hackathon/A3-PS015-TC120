@echo off
setlocal
cd /d "%~dp0"

if exist "dist\TicNet\TicNet.exe" (
    echo Starting TicNet Windows Desktop Application...
    start "" "dist\TicNet\TicNet.exe"
) else (
    echo Launching TicNet via Python environment...
    ".venv\Scripts\python.exe" src\ticnet\desktop\app.py
)

