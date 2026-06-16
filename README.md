# Project Chats

Project Chats is a local desktop tool that finds project-related ChatGPT conversations, helps you review them, and moves approved chats into a shared ChatGPT Project. It is designed for one-off ChatGPT Business/Team cleanup work where each user can only access their own chats — each person runs it on their own account, reviews the results, and ships the approved chats into a shared Project.

The GUI is the primary interface. A CLI is available for scripting.

## Install

```bash
pipx install 'project-chats[browser]'
playwright install chromium
project-chats-gui
```

That's it — no clone, no virtualenv to manage. The first run of the GUI will offer to install the browser automation pieces if they aren't already there.

If you don't have `pipx`, install it with `python3 -m pip install --user pipx && python3 -m pipx ensurepath`. The `[browser]` extra pulls in Playwright; `playwright install chromium` downloads the actual browser binary the move step uses.

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

## Inputs

### ChatGPT Export

Personal ChatGPT exports include `conversations.json`. ChatGPT Business may not expose the same export flow, so this format is supported when available but not required.

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

If you want to modify Project Chats or contribute:

```bash
git clone https://github.com/michaeljeisner/project-chats.git
cd project-chats
python3 scripts/install.py
```

That creates a `.venv` in the repo, installs an editable copy with the browser extra, and writes `./project-chats`, `./project-chats-gui`, and (on macOS) `Project Chats.command` launchers at the repo root.

Run tests:

```bash
python3 -m unittest discover -s tests
```
