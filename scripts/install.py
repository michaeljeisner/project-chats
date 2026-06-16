#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
BIN = VENV / ("Scripts" if os.name == "nt" else "bin")
PYTHON = BIN / ("python.exe" if os.name == "nt" else "python")


def run(command: list[str]) -> None:
    print("$ " + " ".join(str(part) for part in command))
    subprocess.check_call([str(part) for part in command], cwd=ROOT)


def write_launcher(path: Path, command: str) -> None:
    path.write_text(command)
    if os.name != "nt":
        path.chmod(0o755)


def main() -> None:
    if not VENV.exists():
        run([sys.executable, "-m", "venv", VENV])

    run([PYTHON, "-m", "pip", "install", "--upgrade", "pip"])
    run([PYTHON, "-m", "pip", "install", "-e", ".[browser]"])
    run([PYTHON, "-m", "playwright", "install", "chromium"])

    if os.name == "nt":
        write_launcher(
            ROOT / "project-chats.bat",
            "@echo off\r\n\"%~dp0.venv\\Scripts\\project-chats.exe\" %*\r\n",
        )
        write_launcher(
            ROOT / "project-chats-gui.bat",
            "@echo off\r\n\"%~dp0.venv\\Scripts\\project-chats-gui.exe\" %*\r\n",
        )
    else:
        write_launcher(
            ROOT / "project-chats",
            "#!/usr/bin/env sh\nDIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nexec \"$DIR/.venv/bin/project-chats\" \"$@\"\n",
        )
        write_launcher(
            ROOT / "project-chats-gui",
            "#!/usr/bin/env sh\nDIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nexec \"$DIR/.venv/bin/project-chats-gui\" \"$@\"\n",
        )
        write_launcher(
            ROOT / "Project Chats.command",
            "#!/usr/bin/env sh\nDIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nexec \"$DIR/project-chats-gui\"\n",
        )

    print("\nInstalled Project Chats.")
    if os.name == "nt":
        print("Run the GUI with: project-chats-gui.bat")
        print("Run the CLI with: project-chats.bat --help")
    else:
        print("Run the GUI with: ./project-chats-gui")
        print("Run the CLI with: ./project-chats --help")
        print("On macOS, you can also double-click: Project Chats.command")


if __name__ == "__main__":
    main()
