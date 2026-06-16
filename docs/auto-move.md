# Automatic Project Moves

`project-chats auto-move` is optional browser automation for moving approved chats into a ChatGPT Project.

It does not call a hidden ChatGPT API. It opens ChatGPT in a real browser and uses the visible UI, so it can break if ChatGPT changes button labels or menus.

## Install

```bash
python3 -m pip install '.[browser]'
python3 -m playwright install chromium
```

## Recommended First Run

```bash
project-chats auto-move --dry-run
project-chats auto-move --limit 1
```

The first non-dry run opens a browser profile under:

```text
project-chat-run/browser-profile
```

Sign into the ChatGPT account that owns the approved chats. The browser profile is reused on later runs.

## Multi-User Runs

Each user should run this from their own computer or their own browser profile:

```bash
project-chats auto-move --user-label alice
```

If several people share one machine, use different browser profile directories:

```bash
project-chats auto-move --user-label alice --user-data-dir ./profiles/alice
project-chats auto-move --user-label bob --user-data-dir ./profiles/bob
```

## Output

The command appends results to:

```text
project-chat-run/outputs/move_log.csv
```

Statuses include:

- `moved`
- `already_in_project`
- `unknown`
- `skipped`
- `ui_failed`
- `dry_run`

## Troubleshooting

- If login fails, run without `--headless`.
- If the target project is not visible, share the ChatGPT Project with the signed-in user first.
- If every row fails with `ui_failed`, ChatGPT likely changed the menu labels. Move one chat manually and compare the visible labels to the selectors in `project_chats/browser_move.py`.
