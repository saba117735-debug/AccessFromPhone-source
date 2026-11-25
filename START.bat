@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" in the installer, then run START.bat again.
  pause
  exit /b 1
)
if not exist .venv (
  echo First run: installing requirements, this takes a minute...
  python -m venv .venv
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -e .
  if errorlevel 1 (
    echo Install failed. Check your internet connection.
    rmdir /s /q .venv
    pause
    exit /b 1
  )
)
.venv\Scripts\python -m handoff
pause
