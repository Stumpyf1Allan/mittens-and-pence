#!/usr/bin/env python3
"""Double-click entry point: python run_kestrel.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from kestrel.main import main
sys.exit(main())
