# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install (creates `.venv` and writes launcher scripts at repo root):

```bash
python3 scripts/install.py
```

After install, the launchers `./project-chats`, `./project-chats-gui` (and `Project Chats.command` on macOS) exec the venv entry points. The `scripts/run-cli.py` / `scripts/run-gui.py` helpers fail with a clear error if `.venv` is missing — they intentionally do not auto-install.

Run tests (uses stdlib `unittest`, no extra deps):

```bash
python3 -m unittest discover -s tests
# single test
python3 -m unittest tests.test_smoke.SmokeTest.test_full_workflow
```

The full CLI workflow (each step writes into `--workspace`, default `./project-chat-run`):

```bash
./project-chats init --project-name "Atlas" --term Atlas --term fidelity
./project-chats ingest path/to/conversations.json --user-label alice
./project-chats classify
./project-chats build
./project-chats bundle
./project-chats auto-move --dry-run   # then drop --dry-run, optionally --limit 1
```

`python3 -m project_chats ...` is equivalent to `./project-chats ...` and is what the GUI shells out to.

## Architecture

The tool is a local pipeline over a single **workspace directory** (`project-chat-run/` by default). Every CLI subcommand reads and writes files inside this workspace — there is no database or server. The canonical layout is defined by `core.workspace_paths()`:

- `project_profile.json` — created by `init`; holds `project_name`, `terms`, and the score thresholds (`high_confidence_score`, `possible_score`) used by the classifier.
- `data/raw_chats/normalized_chats.json` — produced by `ingest`; the single normalized form every later step reads.
- `outputs/review_queue.{csv,html}` — produced by `classify`; the human edits the `approved` column in the CSV.
- `outputs/{project_brief,decisions,requirements,open_questions}.md`, `source_chats.csv`, `move_queue.html` — produced by `build` from approved rows.
- `outputs/move_log.csv` — produced by `auto-move`.
- `browser-profile/` — persistent Playwright profile used by `auto-move` (created on first run; user signs into ChatGPT inside it).

### Module layout (`project_chats/`)

- `core.py` — the pipeline. All stages (`init_workspace`, `ingest`, `classify`, `build_outputs`, `bundle`) are pure-Python functions taking a workspace `Path` and producing files. Ingest normalizes four input shapes into the same `Chat`/`Message` dataclasses: ChatGPT export `conversations.json`, the project's own normalized JSON list (see `examples/normalized_chats.json` and the README), Markdown, and plain text (one chat per file). The classifier is deliberately conservative: only chats above `high_confidence_score` are auto-approved; possible matches require human review.
- `cli.py` — argparse front end. Each subcommand is a thin wrapper around a `core` (or `browser_move`) function.
- `gui.py` — Tk wrapper. It does **not** import core directly for running steps; it shells out to `python -m project_chats` via `build_cli_command()` and streams output into a log panel. This is load-bearing for testability — `test_gui_command_uses_cli_under_the_hood` asserts the shape of the command. Requires stdlib `tkinter`.
- `browser_move.py` — Playwright-driven UI automation. Optional dependency: `playwright` is only imported inside `auto_move()` so the rest of the CLI works without the `[browser]` extra. `--dry-run` short-circuits before any browser is launched and is what the smoke test exercises. ChatGPT's UI changes without notice, so this is documented as best-effort; do not rely on selectors being stable across releases.

### Conventions to preserve

- Treat the workspace layout as a contract. Anything that reads or writes workspace files should go through `workspace_paths()` rather than hardcoding subpaths.
- Keep `auto-move` behind a guard: no network/browser side effects in dry-run mode, and Playwright must remain an optional import so the base install stays light.
- The GUI must continue to invoke functionality via the CLI module, not by importing `core` directly — preserves a single source of truth for argument parsing and keeps the GUI's log stream meaningful.
