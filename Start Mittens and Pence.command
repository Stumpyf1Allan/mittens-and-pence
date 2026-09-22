#!/bin/bash
# Double-click to run Mittens & Pence on a Mac. Needs Python 3.10+.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "Mittens & Pence needs Python. Install it from https://www.python.org/downloads/ then try again."
  read -n1 -r -p "Press any key to close..."
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Setting Mittens & Pence up for the first time. This takes a minute..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
exec .venv/bin/python run_kestrel.py
