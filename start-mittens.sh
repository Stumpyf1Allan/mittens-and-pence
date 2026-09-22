#!/bin/bash
# Linux launcher.
cd "$(dirname "$0")"
[ -x ".venv/bin/python" ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/python run_kestrel.py "$@"
