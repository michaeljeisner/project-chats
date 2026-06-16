# Desktop GUI

Project Chats includes a small desktop GUI built with Python's standard Tkinter library.

Tkinter must be included in the Python install. On macOS, the official Python.org installer includes it; some Homebrew Python builds do not.

Install and launch it with:

```bash
python3 scripts/install.py
./project-chats-gui
```

On macOS, you can also double-click `Project Chats.command` after running the installer.

Manual launch after package installation:

```bash
project-chats-gui
```

## What The GUI Does

The GUI uses the CLI underneath the hood. Each button runs a command such as:

```bash
python3 -m project_chats --workspace project-chat-run classify
```

The app shows command output in the log panel.

## Workflow

1. Choose a workspace folder.
2. Create a project profile with a project name and comma-separated terms.
3. Choose an input file or folder and enter a user label.
4. Click `Ingest`.
5. Click `Classify`.
6. Open `Review Queue` and edit approved rows if needed.
7. Click `Build Outputs`.
8. Open generated files or click `Bundle Zip`.
9. Use `Auto-Move` when browser automation dependencies are installed.

## Browser Automation

The one-command installer includes browser automation support. Manual install:

```bash
python3 -m pip install '.[browser]'
python3 -m playwright install chromium
```

Keep `Dry run` enabled for the first pass, then run with `Limit` set to `1`.

## Browser And Account Selection

The GUI uses the `auto-move` CLI command underneath the hood.

Default browser:

```text
chrome
```

Default browser profile:

```text
project-chat-run/browser-profile
```

The account is configured by signing into ChatGPT inside that browser profile. The first non-dry run opens the browser. Sign into the account that owns the approved chats, then continue.

For multiple accounts, use different browser profile directories:

```text
profiles/alice
profiles/bob
```

In the GUI, set these in `Run` -> `Auto-Move Approved Chats`:

- `Browser`: browser channel, usually `chrome`
- `Browser profile directory`: the login/profile to use
- `Project override`: optional target project name override
- `Dry run`: test without moving chats
- `Limit`: move only the first N approved chats
