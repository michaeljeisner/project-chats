# Project Chats

Project Chats is a local CLI for finding project-related ChatGPT conversations, generating project memory files, and preparing a move queue for ChatGPT Projects.

It is designed for one-off ChatGPT Business/Team cleanup work where each user can only access their own chats. Each user runs the tool on their own computer/account, reviews the results, and then moves approved chats into a shared ChatGPT Project through the ChatGPT UI.

## What It Does

- Ingests ChatGPT export-style `conversations.json`, normalized chat JSON, Markdown, or text files.
- Scores conversations against a project profile.
- Produces a review queue with evidence snippets.
- Generates project memory files:
  - `project_brief.md`
  - `decisions.md`
  - `requirements.md`
  - `open_questions.md`
  - `source_chats.csv`
- Generates `move_queue.html` with links and per-chat move instructions.
- Optionally opens ChatGPT in a browser and automatically moves approved chats into the selected Project.
- Creates a handoff zip for teammates or a project coordinator.

## What It Does Not Do

- It does not scrape private workspace-wide chats.
- It does not read browser cookies, passwords, or ChatGPT session storage.
- It does not use undocumented ChatGPT APIs.
- It does not use a ChatGPT Project API because OpenAI does not expose one for moving chats. The optional `auto-move` command drives the visible ChatGPT UI in a browser.

## Install

```bash
python3 -m pip install .
```

For browser automation:

```bash
python3 -m pip install '.[browser]'
python3 -m playwright install chromium
```

Or run from a checkout without installing:

```bash
python3 -m project_chats --help
```

## Quick Start

```bash
project-chats init --project-name "Project Atlas" \
  --term "Atlas" --term "launch" --term "fidelity"

project-chats ingest ~/Downloads/conversations.json --user-label michael
project-chats classify
project-chats build
project-chats bundle
```

Open:

```text
project-chat-run/outputs/review_queue.html
project-chat-run/outputs/move_queue.html
```

Edit `project-chat-run/outputs/review_queue.csv` and set `approved=true` only for chats you want moved.

To move approved chats automatically through the ChatGPT UI:

```bash
project-chats auto-move
```

The first run opens a real browser profile at `project-chat-run/browser-profile`. Sign into ChatGPT there when prompted. The command writes `project-chat-run/outputs/move_log.csv`.

## Multi-User Workflow

1. Project coordinator creates a profile:

   ```bash
   project-chats init --project-name "Shared Project" --term "client" --term "repo-name"
   ```

2. Send `project-chat-run/project_profile.json` to each participant.
3. Each participant runs:

   ```bash
   project-chats ingest ./my_chats.json --user-label alice
   project-chats classify
   project-chats build
   project-chats bundle
   ```

4. Each participant reviews `review_queue.html`.
5. Each participant either opens `move_queue.html` and moves approved chats manually, or runs `project-chats auto-move` while signed into ChatGPT.
6. Participants send the generated zip to the coordinator if a consolidated memory pack is needed.

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

## Review

The classifier intentionally favors review over silent automation. High-confidence matches are approved by default; possible matches are not.

Change the `approved` column in `review_queue.csv`, then run:

```bash
project-chats build
```

## Safety

Only run this on chats you are authorized to process. Review generated files before uploading them into a ChatGPT Project or sharing them with teammates.

The `auto-move` command is best-effort UI automation. ChatGPT can change labels or menus without notice, so run `project-chats auto-move --dry-run` first, then use `--limit 1` for a supervised first move.
