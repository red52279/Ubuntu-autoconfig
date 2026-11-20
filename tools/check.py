#!/usr/bin/env python3
"""Read-only installation and architecture check; same detector as install.sh."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    os.execv('/usr/bin/python3', ['python3', '-B', str(ROOT/'scripts/lib/preflight.py'), *sys.argv[1:]])
