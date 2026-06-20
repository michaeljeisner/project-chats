from __future__ import annotations

import csv
import platform
import shutil
import subprocess
import time
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


@dataclass
class AuthOptions:
    workspace: Path
    user_data_dir: Path | None = None
    channel: str = "chrome"
    headless: bool = False
    timeout_s: int = 300
    slow_mo_ms: int = 100


CHATGPT_URL = "https://chatgpt.com/"


def _import_playwright():
    """Import Playwright lazily so the base install stays light."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit(
            "Browser automation requires Playwright. Install with: "
            "python3 -m pip install 'project-chats[browser]' && python3 -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def _profile_dir(workspace: Path, user_data_dir: Path | None) -> Path:
    path = user_data_dir or (workspace / "browser-profile")
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_context(playwright, user_data_dir: Path, channel: str, headless: bool, slow_mo_ms: int):
    return playwright.chromium.launch_persistent_context(
        str(user_data_dir),
        channel=channel,
        headless=headless,
        slow_mo=slow_mo_ms,
    )


def launch_system_browser_for_login(user_data_dir: Path, channel: str, timeout_s: int) -> bool:
    """Open ChatGPT in an installed browser using the same profile directory.

    This avoids putting the interactive login page under Playwright control,
    which can trip anti-bot checks such as Cloudflare Turnstile. Returns True
    when a system browser was launched and closed, False when this platform or
    channel is not supported by the direct launcher.
    """
    system = platform.system()
    if system == "Darwin":
        app_names = {
            "chrome": "Google Chrome",
            "chrome-beta": "Google Chrome Beta",
            "msedge": "Microsoft Edge",
            "chromium": "Chromium",
        }
        app_name = app_names.get(channel)
        if not app_name:
            return False
        command = [
            "open",
            "-W",
            "-n",
            "-a",
            app_name,
            "--args",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--new-window",
            CHATGPT_URL,
        ]
    else:
        executable_names = {
            "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
            "chrome-beta": ["google-chrome-beta"],
            "msedge": ["microsoft-edge", "microsoft-edge-stable"],
            "chromium": ["chromium", "chromium-browser"],
        }
        executable = next(
            (name for name in executable_names.get(channel, []) if shutil.which(name)),
            None,
        )
        if not executable:
            return False
        command = [
            executable,
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--new-window",
            CHATGPT_URL,
        ]

    try:
        subprocess.run(command, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("Timed out waiting for the browser to close. Close it after signing in, then try again.")
        return True
    return True


def is_logged_in(page) -> bool:
    """Best-effort detection of an authenticated ChatGPT session.

    ChatGPT's UI changes without notice, so this leans on a few signals:
    an auth URL means logged out; a visible composer/textbox means logged in;
    explicit Log in / Sign up CTAs mean logged out.
    """
    url = (page.url or "").lower()
    if "auth.openai.com" in url or "/auth/login" in url or "/log-in" in url:
        return False
    try:
        if page.locator("#prompt-textarea").count():
            return True
    except Exception:
        pass
    for name in ("Log in", "Sign up", "Stay logged out"):
        try:
            if page.get_by_role("button", name=name).count():
                return False
        except Exception:
            continue
    try:
        if page.get_by_role("textbox").count():
            return True
    except Exception:
        pass
    return False


def profile_has_session(playwright, user_data_dir: Path, channel: str) -> bool:
    try:
        context = launch_context(playwright, user_data_dir, channel, headless=True, slow_mo_ms=0)
    except Exception:
        return False
    try:
        return bool(session_cookie_header(context.cookies("https://chatgpt.com")))
    finally:
        context.close()


def login(options: AuthOptions) -> int:
    """Open a real browser so the user can sign into ChatGPT, then persist the session.

    Returns 0 once a signed-in session is detected, 2 on timeout.
    """
    sync_playwright, _ = _import_playwright()
    user_data_dir = _profile_dir(options.workspace, options.user_data_dir)
    with sync_playwright() as p:
        if not options.headless:
            print(
                "Opening ChatGPT in your installed browser. Sign in there, then close that browser window "
                "so Project Chats can verify the saved session.",
                flush=True,
            )
            if launch_system_browser_for_login(user_data_dir, options.channel, options.timeout_s):
                if profile_has_session(p, user_data_dir, options.channel):
                    print("Signed in to ChatGPT.")
                    return 0
                print("Sign-in was not detected. Try again after completing the browser login.")
                return 2

        context = launch_context(p, user_data_dir, options.channel, options.headless, options.slow_mo_ms)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(CHATGPT_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            if is_logged_in(page):
                print("Already signed in to ChatGPT.")
                return 0
            print(
                "A browser window opened. Sign in to ChatGPT there — "
                "this window closes automatically once you're signed in.",
                flush=True,
            )
            deadline = time.monotonic() + options.timeout_s
            while time.monotonic() < deadline:
                page.wait_for_timeout(1500)
                if is_logged_in(page):
                    print("Signed in to ChatGPT.")
                    return 0
            print("Timed out waiting for sign-in. Try again.")
            return 2
        finally:
            context.close()


SESSION_COOKIE_NAMES = (
    "__Secure-next-auth.session-token",
    "__Secure-next-auth.session-token.0",
    "__Secure-next-auth.session-token.1",
)


def session_cookie_header(cookies: list[dict]) -> str:
    """Build the Cookie header from a Playwright cookie list.

    ChatGPT sometimes splits the session token across .0/.1 cookies; replay
    whichever parts are present, in order, exactly as the browser sends them.
    """
    found = {c["name"]: c["value"] for c in cookies if c.get("name") in SESSION_COOKIE_NAMES}
    if not found:
        return ""
    parts = [f"{name}={found[name]}" for name in SESSION_COOKIE_NAMES if name in found]
    return "; ".join(parts)


def extract_session_cookie(options: AuthOptions) -> str:
    """Read the ChatGPT session cookie from the signed-in browser profile.

    Launches the persistent profile headless (no user interaction needed once
    signed in) and returns a ready-to-use Cookie header. Raises if the profile
    has no session — the user must sign in first.
    """
    sync_playwright, _ = _import_playwright()
    user_data_dir = _profile_dir(options.workspace, options.user_data_dir)
    with sync_playwright() as p:
        context = launch_context(p, user_data_dir, options.channel, headless=True, slow_mo_ms=0)
        try:
            cookies = context.cookies("https://chatgpt.com")
        finally:
            context.close()
    header = session_cookie_header(cookies)
    if not header:
        raise SystemExit(
            "No ChatGPT session found in the browser profile. "
            "Sign in first (GUI: 'Sign in to ChatGPT'; CLI: project-chats login)."
        )
    return header


def check_login(options: AuthOptions) -> int:
    """Headless best-effort check. Prints signed_in/signed_out, exits 0/1."""
    sync_playwright, _ = _import_playwright()
    user_data_dir = _profile_dir(options.workspace, options.user_data_dir)
    with sync_playwright() as p:
        if profile_has_session(p, user_data_dir, options.channel):
            print("signed_in")
            return 0
        try:
            context = launch_context(p, user_data_dir, options.channel, headless=True, slow_mo_ms=0)
        except Exception:
            print("signed_out")
            return 1
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(CHATGPT_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            ok = is_logged_in(page)
        finally:
            context.close()
    print("signed_in" if ok else "signed_out")
    return 0 if ok else 1


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

    sync_playwright, PlaywrightTimeoutError = _import_playwright()

    records = []
    user_data_dir = _profile_dir(options.workspace, options.user_data_dir)

    with sync_playwright() as p:
        context = launch_context(p, user_data_dir, options.channel, options.headless, options.slow_mo_ms)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(CHATGPT_URL, wait_until="domcontentloaded")
            wait_for_login(page, options.headless)

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
        finally:
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


def wait_for_login(page, headless: bool, timeout_s: float = 120.0) -> None:
    """Ensure the ChatGPT session is signed in before moving chats.

    Polls the page instead of reading stdin, so this works when auto-move runs
    as a GUI subprocess. Headless runs can't show a login form, so they fail
    fast with a clear message pointing at the sign-in step.
    """
    if is_logged_in(page):
        return
    if headless:
        raise RuntimeError(
            "Not signed in to ChatGPT. Sign in first "
            "(GUI: 'Sign in to ChatGPT'; CLI: project-chats login)."
        )
    print("Sign in to ChatGPT in the opened browser window. Waiting…", flush=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        page.wait_for_timeout(1500)
        if is_logged_in(page):
            print("Signed in.", flush=True)
            return
    raise RuntimeError("Timed out waiting for ChatGPT sign-in. Run sign-in, then retry.")


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
