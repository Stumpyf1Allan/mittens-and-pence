@echo off
REM ===================================================================
REM  Double-click this to run Mittens and Pence without building anything.
REM  It needs Python 3.10+ installed from python.org (tick "Add Python
REM  to PATH" on the first screen of the installer).
REM  The first run takes a minute while it sets itself up; after that
REM  it opens straight away.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Mittens ^& Pence

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Mittens and Pence needs Python, and it isn't installed on this PC yet.
  echo.
  echo   1. Go to  https://www.python.org/downloads/
  echo   2. Download the latest version for Windows and run it.
  echo   3. On the FIRST screen, tick "Add python.exe to PATH".
  echo   4. Finish the install, then double-click this file again.
  echo.
  pause
  start https://www.python.org/downloads/
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo   Setting Mittens and Pence up for the first time. This takes a minute...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

echo   Starting Mittens and Pence...
python run_kestrel.py
if errorlevel 1 (
  echo.
  echo   Mittens and Pence stopped unexpectedly. The details are in the log file:
  echo   %%LOCALAPPDATA%%\Mittens and Pence\logs\mittens.log
  pause
)
