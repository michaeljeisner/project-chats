from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import (
    DEFAULT_WORKSPACE,
    ProjectChatsError,
    build_outputs,
    bundle,
    classify,
    copy_example,
    ingest,
    init_workspace,
)
from .browser_move import AuthOptions, MoveOptions, auto_move, check_login, login
from .chatgpt_api import FetchOptions, run_fetch


def main(argv: list[str] | None = None) -> None:
    try:
        _dispatch(argv)
    except ProjectChatsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _dispatch(argv: list[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="project-chats",
        description="Find project-related ChatGPT conversations and generate project memory/move queues.",
    )
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="Run workspace directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a project profile and run workspace.")
    init.add_argument("--project-name", required=True)
    init.add_argument("--term", action="append", default=[], help="Project keyword/alias/person/repo/client/domain. Repeatable.")
    init.add_argument("--force", action="store_true")

    ingest_cmd = sub.add_parser("ingest", help="Ingest JSON, Markdown, text, or a directory of files.")
    ingest_cmd.add_argument("inputs", nargs="+", type=Path)
    ingest_cmd.add_argument("--user-label", required=True, help="Label for the account/user these chats came from.")

    sub.add_parser("classify", help="Score chats and write review_queue.csv/html.")
    sub.add_parser("build", help="Build approved project memory files and move queue.")
    sub.add_parser("bundle", help="Zip the profile and outputs for handoff.")

    move = sub.add_parser("auto-move", help="Best-effort browser automation to move approved chats into a ChatGPT Project.")
    move.add_argument("--user-label", help="Only move rows for this user label.")
    move.add_argument("--project-name", help="Override the project name from project_profile.json.")
    move.add_argument("--user-data-dir", type=Path, help="Playwright browser profile directory. Defaults to WORKSPACE/browser-profile.")
    move.add_argument("--channel", default="chrome", help="Browser channel for Playwright. Default: chrome.")
    move.add_argument("--headless", action="store_true", help="Run browser headless. Not recommended for first login.")
    move.add_argument("--dry-run", action="store_true", help="Write move_log.csv without opening a browser.")
    move.add_argument("--limit", type=int, help="Maximum approved chats to process.")
    move.add_argument("--slow-mo-ms", type=int, default=150, help="Delay between browser actions in milliseconds.")

    fetch_cmd = sub.add_parser("fetch", help="Bulk-download your ChatGPT chats via the web API (Team/Business, no export needed).")
    fetch_cmd.add_argument("--user-label", required=True, help="Label for the account/user these chats came from.")
    fetch_cmd.add_argument("--session-token", help="Raw __Secure-next-auth.session-token value. Omit to use the signed-in browser profile.")
    fetch_cmd.add_argument("--account-id", help="ChatGPT workspace/account id. Auto-detected if omitted.")
    fetch_cmd.add_argument("--user-data-dir", type=Path, help="Browser profile to read the session from. Defaults to WORKSPACE/browser-profile.")
    fetch_cmd.add_argument("--channel", default="chrome", help="Browser channel for Playwright. Default: chrome.")
    fetch_cmd.add_argument("--limit", type=int, help="Maximum conversations to download.")
    fetch_cmd.add_argument("--delay", type=float, default=0.5, help="Delay between API requests in seconds. Default: 0.5.")

    login_cmd = sub.add_parser("login", help="Sign into ChatGPT in the auto-move browser profile.")
    login_cmd.add_argument("--user-data-dir", type=Path, help="Playwright browser profile directory. Defaults to WORKSPACE/browser-profile.")
    login_cmd.add_argument("--channel", default="chrome", help="Browser channel for Playwright. Default: chrome.")
    login_cmd.add_argument("--check", action="store_true", help="Headless status probe. Prints signed_in/signed_out; exits 0 if signed in, 1 if not.")
    login_cmd.add_argument("--timeout", type=int, default=300, help="Seconds to wait for interactive sign-in. Default: 300.")
    login_cmd.add_argument("--slow-mo-ms", type=int, default=100, help="Delay between browser actions in milliseconds.")

    example = sub.add_parser("copy-example", help="Copy sample input files to a destination directory.")
    example.add_argument("destination", type=Path)

    args = parser.parse_args(argv)

    if args.command == "init":
        path = init_workspace(args.workspace, args.project_name, args.term, args.force)
        print(f"Created {path}")
    elif args.command == "ingest":
        path = ingest(args.inputs, args.workspace, args.user_label)
        print(f"Wrote normalized chats to {path}")
    elif args.command == "classify":
        path = classify(args.workspace)
        print(f"Wrote review queue to {path}")
    elif args.command == "build":
        paths = build_outputs(args.workspace)
        for path in paths:
            print(f"Wrote {path}")
    elif args.command == "bundle":
        path = bundle(args.workspace)
        print(f"Wrote {path}")
    elif args.command == "auto-move":
        path = auto_move(
            MoveOptions(
                workspace=args.workspace,
                user_label=args.user_label,
                project_name=args.project_name,
                user_data_dir=args.user_data_dir,
                channel=args.channel,
                headless=args.headless,
                dry_run=args.dry_run,
                limit=args.limit,
                slow_mo_ms=args.slow_mo_ms,
            )
        )
        print(f"Wrote move log to {path}")
    elif args.command == "fetch":
        path = run_fetch(
            FetchOptions(
                workspace=args.workspace,
                user_label=args.user_label,
                session_token=args.session_token,
                account_id=args.account_id,
                user_data_dir=args.user_data_dir,
                channel=args.channel,
                limit=args.limit,
                delay=args.delay,
            ),
            progress=print,
        )
        print(f"Wrote fetched chats to {path}")
    elif args.command == "login":
        options = AuthOptions(
            workspace=args.workspace,
            user_data_dir=args.user_data_dir,
            channel=args.channel,
            timeout_s=args.timeout,
            slow_mo_ms=args.slow_mo_ms,
        )
        code = check_login(options) if args.check else login(options)
        sys.exit(code)
    elif args.command == "copy-example":
        copy_example(args.destination)
        print(f"Copied examples to {args.destination}")


if __name__ == "__main__":
    main()
