#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
CLI = BIN / ("project-chats.exe" if os.name == "nt" else "project-chats")


def main() -> int:
    if not CLI.exists():
        print("Project Chats is not installed yet. Run: python3 scripts/install.py", file=sys.stderr)
        return 1
    return subprocess.call([str(CLI), *sys.argv[1:]], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
