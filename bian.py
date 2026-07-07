#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility CLI entry for the Binance futures analyzer."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bian_dashboard.analyzer import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

