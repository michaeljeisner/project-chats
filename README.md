# Project Chats

Project Chats is a local desktop tool that finds project-related ChatGPT conversations, helps you review them, and moves approved chats into a shared ChatGPT Project. It is designed for one-off ChatGPT Business/Team cleanup work where each user can only access their own chats — each person runs it on their own account, reviews the results, and ships the approved chats into a shared Project.

The GUI is the primary interface. A CLI is available for scripting.

## Install

```bash
pipx install 'project-chats[browser]'
playwright install chromium
project-chats-gui
```

No clone, no virtualenv to manage. The `[browser]` extra installs Playwright; `playwright install chromium` downloads the browser binary used by Auto-Move. The GUI will also offer to run this for you on first launch.

### macOS + Homebrew Python

Homebrew Python does not include Tkinter (required by the GUI). Install it first:

```bash
brew install python-tk@3.14   # match your Python version
pipx install 'project-chats[browser]'
playwright install chromium
project-chats-gui
```

If you already installed project-chats and then installed python-tk, run `pipx reinstall project-chats` to pick it up.

The official [python.org](https://www.python.org/downloads/) macOS installer includes Tkinter and avoids this step entirely.

If you don't have `pipx`: `brew install pipx && pipx ensurepath`.

## What It Does

- Imports a ChatGPT export (`conversations.json`), normalized JSON, Markdown, or text files.
- Scores conversations against a project profile (name + terms).
- Lets you review high-confidence and possible matches in-app, with snippets.
- Generates project memory files from approved chats:
  - `project_brief.md`
  - `decisions.md`
  - `requirements.md`
  - `open_questions.md`
  - `source_chats.csv`
- Generates `move_queue.html` with links and per-chat move instructions.
- Optionally drives ChatGPT in a browser to move approved chats into the selected Project.
- Creates a handoff zip for teammates or a project coordinator.

## What It Does Not Do

- Does not scrape workspace-wide chats — only what your account can see.
- Does not read browser cookies, passwords, or ChatGPT session storage.
- Does not use undocumented ChatGPT APIs.
- Does not use a ChatGPT Project API (OpenAI does not expose one). The optional `auto-move` step drives the visible ChatGPT UI in a browser.

## Using the GUI

Launch:

```bash
project-chats-gui
```

The GUI walks you through four steps:

1. **New Project** — name it and add terms (project names, aliases, people, repos, clients, domains, or any words that identify relevant chats).
2. **Import Chats** — drop a `conversations.json`, normalized JSON, Markdown, or text. Classification runs automatically.
3. **Review** — check the candidates, approve the right ones, hit Save & Continue.
4. **Move** — open the move queue HTML for manual moving, or use Auto-Move to drive ChatGPT in a real browser.

The browser used by Auto-Move is a separate Playwright profile, not your everyday Chrome — sign into the ChatGPT account you want to use the first time. You can point at a different profile directory from Settings to use a different account.

## Quick Start (CLI)

The CLI mirrors what the GUI does and is useful for scripting or headless runs:

```bash
project-chats init --project-name "Project Atlas" \
  --term "Atlas" --term "launch" --term "fidelity"

project-chats ingest ~/Downloads/conversations.json --user-label michael
project-chats classify
project-chats build
project-chats bundle
```

This writes everything under `./project-chat-run/`:

```text
project-chat-run/outputs/review_queue.html
project-chat-run/outputs/move_queue.html
```

Edit `project-chat-run/outputs/review_queue.csv` and set `approved=true` only for chats you want moved, then rerun `project-chats build`. The GUI does this in-app.

To move approved chats automatically through the ChatGPT UI:

```bash
project-chats auto-move
```

The first run opens a real browser profile at `project-chat-run/browser-profile`. Sign into ChatGPT there when prompted. The command writes `project-chat-run/outputs/move_log.csv`.

To use a specific browser profile/account from the CLI:

```bash
project-chats auto-move --user-data-dir ./profiles/alice
```

## Multi-User Workflow

1. Project coordinator creates a profile:

   ```bash
   project-chats init --project-name "Shared Project" --term "client" --term "repo-name"
   ```

2. Send `project-chat-run/project_profile.json` to each participant.
3. Each participant runs the same workflow on their own account.
4. Each participant reviews and either uses the move queue HTML manually or runs `project-chats auto-move` while signed in.
5. Participants send the generated zip to the coordinator if a consolidated memory pack is needed.

## Getting your chats in

### Fetch directly from ChatGPT (Team/Business — no export)

ChatGPT Team/Business accounts don't offer a per-user data export. For those, Project Chats can download your own conversations directly through ChatGPT's web API — the same calls the web app makes:

1. Sign in once (Move step → **Sign in to ChatGPT**, or `project-chats login`).
2. Fetch (Import step → **Fetch my ChatGPT chats**, or `project-chats fetch --user-label you`).

The session is read from the browser profile you signed into — you never copy a cookie by hand. From the CLI you can also pass a token manually with `--session-token` if you prefer.

```bash
project-chats login
project-chats fetch --user-label michael
project-chats classify
```

This is best-effort and uses an undocumented internal API, so — like Auto-Move — it can break when ChatGPT changes. It only ever reads *your own* conversations, the same ones you can see in your browser.

### ChatGPT export (personal accounts)

Personal ChatGPT exports include `conversations.json`. Import it from the Import step or with `project-chats ingest conversations.json --user-label you`.

### Normalized JSON

```json
[
  {
    "user_label": "alice",
    "conversation_id": "abc123",
    "title": "Migration notes",
    "url": "https://chatgpt.com/c/abc123",
    "messages": [
      {"author": "user", "text": "Project Atlas rollout plan..."},
      {"author": "assistant", "text": "Decision: use staged rollout..."}
    ]
  }
]
```

### Markdown/Text

Markdown and text files are ingested as one chat per file. The file name becomes the chat title.

## Safety

Only run this on chats you are authorized to process. Review the generated files before uploading them into a ChatGPT Project or sharing them with teammates.

The `auto-move` command is best-effort UI automation. ChatGPT can change labels or menus without notice, so run `project-chats auto-move --dry-run` first, then use `--limit 1` for a supervised first move.

## Hack on It

```bash
git clone https://github.com/michaeljeisner/project-chats.git
cd project-chats
python3 scripts/install.py
```

That creates a `.venv`, installs an editable copy with the browser extra, and writes `./project-chats-gui`, `./project-chats`, and (macOS) `Project Chats.command` launchers.

Run tests:

```bash
python3 -m unittest discover -s tests
```
