# Install and Run

## Simple Setup

```bash
git clone https://github.com/michaeljeisner/project-chats.git
cd project-chats
python3 scripts/install.py
```

This creates a local `.venv`, installs Project Chats with browser automation support, installs Playwright's Chromium browser, and creates launchers.

## Run The GUI

```bash
./project-chats-gui
```

On macOS, you can also double-click:

```text
Project Chats.command
```

## Run The CLI

```bash
./project-chats --help
```

## No Launcher Yet?

If the launcher files do not exist, run:

```bash
python3 scripts/install.py
```

You can also use the helper scripts:

```bash
python3 scripts/run-gui.py
python3 scripts/run-cli.py --help
```

## Manual Package Install

```bash
python3 -m pip install '.[browser]'
python3 -m playwright install chromium
project-chats-gui
project-chats --help
```
