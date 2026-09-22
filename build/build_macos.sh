#!/bin/bash
# Build Mittens and Pence for macOS.   bash build/build_macos.sh
#
# Makes two things:
#   dist/Mittens and Pence.app                        - the normal Mac app. Keep this.
#   dist/send/Mittens and Pence-macos-<arch>.app.zip  - the same app, zipped, with a
#                                                       Read me beside it, to send on.
#
# The zip is built for the processor of the Mac you are sitting at, and its name says
# which. An Apple Silicon build will not start at all on an Intel Mac and an Intel one
# needs Rosetta on Apple Silicon, so "the Mac version" is two files, and the only way
# to make both is to run this on both kinds of Mac. GitHub does exactly that for you
# on every release — see .github/workflows/release.yml. This script is for building
# one by hand.
set -e
cd "$(dirname "$0")/.."

echo
echo "  Mittens & Pence — building the Mac app"
echo "  ------------------------------"

command -v python3 >/dev/null || { echo "  Install Python 3.10+ first."; exit 1; }

echo "  [1/5] Making a private Python environment..."
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate

echo "  [2/5] Installing what Mittens and Pence needs..."
python -m pip install --quiet --upgrade pip
# requirements-dev.txt pulls in requirements.txt AND the two things the build itself
# needs: pytest to run the checks, pyinstaller to make the app.
python -m pip install --quiet -r requirements-dev.txt
python -m pip install --quiet pywebview keyring cryptography

echo "  [3/5] Checking everything still works..."
python -c "import pytest" 2>/dev/null || {
  echo "  pytest didn't install, so the checks can't run."
  echo "  Try:  .venv/bin/python -m pip install pytest"
  exit 1
}
python -X warn_default_encoding -m pytest tests -q

# A copy built with no update address never looks for a new version — much more
# often a forgotten step than a decision, and unfixable afterwards on someone
# else's Mac.
if ! python3 -c "import sys;sys.path.insert(0,'.');from kestrel import config;sys.exit(0 if config.UPDATE_MANIFEST_URL else 1)"; then
  echo
  echo "  Note: UPDATE_MANIFEST_URL has been emptied in kestrel/config.py, so the"
  echo "  you send out will never look for updates. That is fine if you meant it."
  echo "  If you didn't, see docs/PUBLISHING.md — it has to be done BEFORE this build."
  echo
fi

echo "  [4/5] Building the app..."
unset MITTENS_ONEFILE
python -m PyInstaller build/mittens.spec --noconfirm --clean

echo "  [5/5] Packing it up to send..."
# ditto, not zip. A .app is a folder of symlinks and permission bits, and a plain
# `zip -r` quietly flattens them: the bundle unzips looking perfectly normal and then
# refuses to launch on the other person's Mac, which is the worst possible moment to
# find out. ditto is Apple's own tool and keeps the bundle intact.
ARCH=$(uname -m)                     # arm64 on Apple Silicon, x86_64 on Intel
mkdir -p dist/send
ZIP="dist/send/Mittens and Pence-macos-$ARCH.app.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "dist/Mittens and Pence.app" "$ZIP"
cp "build/Read me first.txt" "dist/send/Read me first.txt"

echo
echo "  Done."
echo
echo "  FOR YOURSELF:  $(pwd)/dist/Mittens and Pence.app"
echo "  TO SEND OUT:   $ZIP"
echo "                 (plus the Read me beside it)"
echo
echo "  That zip is the $ARCH build and ONLY runs on a $ARCH Mac. The other half of"
echo "  the family needs the other one, built on the other kind of Mac — or let"
echo "  GitHub build both: Actions > Release > Run workflow."
echo
echo "  First launch on any Mac: it will refuse. Then System Settings >"
echo "  Privacy & Security > Open Anyway. That's on the Read me."
echo
