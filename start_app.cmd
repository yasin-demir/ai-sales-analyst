@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See README.md setup instructions.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m src.app
if errorlevel 1 pause
