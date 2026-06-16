from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .core import load_profile, read_review_rows, workspace_paths


MOVE_FIELDS = [
    "timestamp",
    "user_label",
    "conversation_id",
    "title",
    "url",
    "status",
    "detail",
]


@dataclass
class MoveOptions:
    workspace: Path
    user_label: str | None = None
    project_name: str | None = None
    user_data_dir: Path | None = None
    channel: str = "chrome"
    headless: bool = False
    dry_run: bool = False
    limit: int | None = None
    slow_mo_ms: int = 150


def auto_move(options: MoveOptions) -> Path:
    profile = load_profile(options.workspace)
    project_name = options.project_name or profile["project_name"]
    rows = approved_rows(options.workspace, options.user_label, options.limit)
    output_path = workspace_paths(options.workspace)["outputs"] / "move_log.csv"

    if options.dry_run:
        records = [
            move_record(row, "dry_run", f"Would move to project: {project_name}")
            for row in rows
        ]
        append_move_records(output_path, records)
        return output_path

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Browser automation requires Playwright. Install with: "
            "python3 -m pip install 'project-chats[browser]' && python3 -m playwright install chromium"
        ) from exc

    records = []
    user_data_dir = options.user_data_dir or (options.workspace / "browser-profile")
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            channel=options.channel,
            headless=options.headless,
            slow_mo=options.slow_mo_ms,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        wait_for_login(page)

        for row in rows:
            url = row.get("url") or ""
            if not url:
                records.append(move_record(row, "skipped", "No chat URL available."))
                continue
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
                status, detail = move_one_chat(page, project_name, PlaywrightTimeoutError)
                records.append(move_record(row, status, detail))
            except Exception as exc:  # noqa: BLE001 - log per-row failures and continue.
                records.append(move_record(row, "ui_failed", str(exc)[:500]))

        context.close()

    append_move_records(output_path, records)
    return output_path


def approved_rows(workspace: Path, user_label: str | None, limit: int | None) -> list[dict]:
    rows = [
        row
        for row in read_review_rows(workspace)
        if str(row.get("approved", "")).lower() == "true"
    ]
    if user_label:
        rows = [row for row in rows if row.get("user_label") == user_label]
    rows = [
        row
        for row in rows
        if row.get("move_status") not in {"moved", "already_in_project"}
    ]
    return rows[:limit] if limit else rows


def wait_for_login(page) -> None:
    if page.get_by_role("textbox").count() or page.get_by_role("button", name="Search chats").count():
        return
    print("Sign into ChatGPT in the opened browser window, then press Enter here.")
    input()
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded")


def move_one_chat(page, project_name: str, timeout_error_type) -> tuple[str, str]:
    if page.get_by_text(project_name, exact=True).count():
        return "already_in_project", f"Project name already visible on page: {project_name}"

    open_chat_menu(page, timeout_error_type)
    click_menu_item(page, ["Move to project", "Move to Project", "Add to project", "Add to Project"], timeout_error_type)
    choose_project(page, project_name, timeout_error_type)
    confirm_if_needed(page)
    page.wait_for_timeout(1200)

    if page.get_by_text(project_name, exact=True).count():
        return "moved", f"Moved to project: {project_name}"
    return "unknown", "Move flow completed, but project name was not visible afterward."


def open_chat_menu(page, timeout_error_type) -> None:
    labels = [
        "Open conversation options",
        "Open chat options",
        "Conversation options",
        "Chat options",
        "More",
        "More options",
    ]
    for label in labels:
        locator = page.get_by_role("button", name=label)
        if click_first_visible(locator):
            return

    buttons = page.locator("button").all()
    for button in reversed(buttons[-20:]):
        try:
            text = (button.inner_text(timeout=500) or "").strip()
            aria = button.get_attribute("aria-label", timeout=500) or ""
            if "⋯" in text or "…" in text or aria.lower() in {"more", "more options"}:
                button.click(timeout=1500)
                return
        except timeout_error_type:
            continue

    raise RuntimeError("Could not find the chat options menu.")


def click_menu_item(page, labels: list[str], timeout_error_type) -> None:
    for label in labels:
        for role in ("menuitem", "button"):
            locator = page.get_by_role(role, name=label)
            if click_first_visible(locator):
                return
        locator = page.get_by_text(label, exact=True)
        if click_first_visible(locator):
            return
    raise RuntimeError(f"Could not find any menu item: {', '.join(labels)}")


def choose_project(page, project_name: str, timeout_error_type) -> None:
    candidates = [
        page.get_by_role("option", name=project_name),
        page.get_by_role("menuitem", name=project_name),
        page.get_by_role("button", name=project_name),
        page.get_by_text(project_name, exact=True),
    ]
    for locator in candidates:
        if click_first_visible(locator):
            return

    search_boxes = [
        page.get_by_placeholder("Search projects"),
        page.get_by_placeholder("Search projects..."),
        page.get_by_role("textbox"),
    ]
    for box in search_boxes:
        if fill_first_visible(box, project_name):
            page.wait_for_timeout(600)
            if click_first_visible(page.get_by_text(project_name, exact=True)):
                return

    raise RuntimeError(f"Could not select project: {project_name}")


def confirm_if_needed(page) -> None:
    for label in ("Move", "Add", "Confirm", "Done"):
        locator = page.get_by_role("button", name=label)
        if click_first_visible(locator):
            return


def click_first_visible(locator) -> bool:
    count = min(locator.count(), 8)
    for idx in range(count):
        item = locator.nth(idx)
        if item.is_visible():
            item.click(timeout=3000)
            return True
    return False


def fill_first_visible(locator, value: str) -> bool:
    count = min(locator.count(), 8)
    for idx in range(count):
        item = locator.nth(idx)
        if item.is_visible():
            item.fill(value, timeout=3000)
            return True
    return False


def move_record(row: dict, status: str, detail: str) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_label": row.get("user_label", ""),
        "conversation_id": row.get("conversation_id", ""),
        "title": row.get("title", ""),
        "url": row.get("url", ""),
        "status": status,
        "detail": detail,
    }


def append_move_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MOVE_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerows(records)
