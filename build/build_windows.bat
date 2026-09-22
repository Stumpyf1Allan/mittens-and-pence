@echo off
REM ===================================================================
REM  Build Mittens and Pence for Windows.
REM
REM  You need Python 3.10 or newer from python.org, with "Add Python to
REM  PATH" ticked during install. Then just double-click this file.
REM
REM  It makes two things:
REM    dist\Mittens and Pence\Mittens and Pence.exe   - a folder. Fastest to start. Keep this
REM                                 one for your own machine.
REM    dist\send\Mittens and Pence.exe      - ONE file, ~40 MB, with a Read me
REM                                 first.txt beside it. This is the one
REM                                 you send to the family.
REM
REM  Nothing is installed system-wide and nothing is sent anywhere.
REM ===================================================================
setlocal
cd /d "%~dp0\.."

REM A build inside OneDrive or Dropbox can fail with file-lock errors, because the
REM sync client grabs files while PyInstaller is still writing them. Warn, don't
REM block — it usually works, and it is worth knowing what to blame if it doesn't.
echo %CD% | findstr /i "OneDrive Dropbox GoogleDrive" >nul
if not errorlevel 1 (
  echo   Note: this folder is inside OneDrive or another syncing folder.
  echo   If the build fails with a "permission denied" or "file in use" error,
  echo   copy the whole Mittens and Pence folder to your Desktop and build it there.
  echo.
)

echo.
echo   Mittens ^& Pence - building the Windows app
echo   ----------------------------------
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   Python was not found on this PC.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add Python to PATH" on the first screen, then run this again.
  echo.
  pause
  exit /b 1
)

echo   [1/5] Making a private Python environment...
if not exist ".venv" python -m venv .venv
call .venv\Scripts\activate.bat

echo   [2/5] Installing what Mittens and Pence needs...
python -m pip install --quiet --upgrade pip
REM requirements-dev.txt pulls in requirements.txt AND the two things the build
REM itself needs: pytest to run the checks, pyinstaller to make the .exe. This
REM used to install requirements.txt only, so step 3 fell over with
REM "No module named pytest" on every clean machine.
python -m pip install --quiet -r requirements-dev.txt
if errorlevel 1 (
  echo.
  echo   Couldn't install what's needed. Usually that means no internet, or a
  echo   company laptop blocking pip. Check you can reach the internet and
  echo   run this again.
  pause
  exit /b 1
)
python -m pip install --quiet pywebview keyring cryptography

echo   [3/5] Checking everything still works...
python -c "import pytest" 2>nul
if errorlevel 1 (
  echo.
  echo   pytest didn't install, so the checks can't run.
  echo   Try:  .venv\Scripts\python.exe -m pip install pytest
  echo   then run this again.
  pause
  exit /b 1
)
REM -X warn_default_encoding makes Python flag any text read that relies on
REM the platform's default encoding — the thing that differs between the PC
REM this is built on and wherever the code was written.
python -X warn_default_encoding -m pytest tests -q
if errorlevel 1 (
  echo.
  echo   Some checks failed. The build stops here so you don't ship
  echo   something broken. Send the output above to whoever maintains it.
  pause
  exit /b 1
)

REM A copy built with no update address never looks for a new version. That is a
REM perfectly valid way to ship, but it is much more often a forgotten step than a
REM decision, and it cannot be fixed after the fact on somebody else's PC.
python -c "import sys;sys.path.insert(0,'.');from kestrel import config;sys.exit(0 if config.UPDATE_MANIFEST_URL else 1)"
if errorlevel 1 (
  echo.
  echo   Note: UPDATE_MANIFEST_URL has been emptied in kestrel\config.py, so the
  echo   you send out will never look for updates. That is fine if you meant it.
  echo   If you didn't, see docs\PUBLISHING.md - it is a five-minute setup, and
  echo   it has to be done BEFORE this build.
  echo.
)

echo   [4/5] Building the folder version (about two minutes)...
set MITTENS_ONEFILE=
REM `python -m PyInstaller` rather than the bare `pyinstaller` command: it uses this
REM environment's interpreter whether or not the Scripts folder made it onto PATH.
python -m PyInstaller build\mittens.spec --noconfirm --clean
if errorlevel 1 (
  echo   The build failed - see the messages above.
  pause
  exit /b 1
)

echo   [5/5] Building the single file to send (another two minutes)...
set MITTENS_ONEFILE=1
python -m PyInstaller build\mittens.spec --noconfirm --clean --distpath dist\send --workpath build\work-onefile
REM Check the result BEFORE clearing the variable: `set` succeeds, which resets
REM errorlevel to 0, so a failed build here used to be reported as a success.
if errorlevel 1 (
  set MITTENS_ONEFILE=
  echo   The single-file build failed - see the messages above.
  pause
  exit /b 1
)
set MITTENS_ONEFILE=

copy /y "build\Read me first.txt" "dist\send\Read me first.txt" >nul

echo.
echo   Done.
echo.
echo   FOR YOURSELF:   dist\Mittens and Pence\Mittens and Pence.exe
echo                   (the whole folder has to stay together)
echo.
echo   TO SEND OUT:    dist\send\Mittens and Pence.exe
echo                   plus "Read me first.txt" in the same folder.
echo                   That one .exe is everything - no folder needed.
echo.
echo   Put those two files on OneDrive, Google Drive or Dropbox and send
echo   the link. Email will block a .exe attachment, so a link it is.
echo.
echo   The first time anyone runs it Windows says "Windows protected
echo   your PC" because it isn't code-signed. More info, then Run anyway.
echo   That is expected - it is on the Read me.
echo.
pause
