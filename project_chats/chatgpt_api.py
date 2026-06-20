"""ChatGPT internal backend-api client for bulk export.

ChatGPT Business/Team accounts do not offer a per-user data export, so this
talks to the same internal API the web app uses: exchange the session cookie
for a short-lived access token, page through the conversation list, then pull
each conversation's full message tree. The per-conversation response uses the
same `mapping` structure as the official export, so core.normalize_chatgpt_export
parses it directly.

This is best-effort and undocumented — the endpoints can change without notice,
same caveat as auto-move. The session cookie is normally extracted from the
signed-in browser profile (see browser_move.extract_session_cookie); a manual
token is supported as a fallback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BASE_URL = "https://chatgpt.com/backend-api"
SESSION_URL = "https://chatgpt.com/api/auth/session"
ACCOUNTS_URL = f"{BASE_URL}/accounts/check/v4-2023-04-27"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

ProgressFn = Callable[[str], None]


@dataclass
class FetchOptions:
    workspace: Path
    user_label: str
    session_token: str | None = None  # raw single-cookie value (manual fallback)
    account_id: str | None = None
    limit: int | None = None
    user_data_dir: Path | None = None
    channel: str = "chrome"
    delay: float = 0.5
    page_size: int = 100


def _requests():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit(
            "Fetching from ChatGPT requires the 'requests' package. Install with: "
            "python3 -m pip install 'project-chats[browser]'"
        ) from exc
    return requests


def _make_session(cookie_header: str):
    requests = _requests()
    session = requests.Session()
    session.headers.update(
        {
            "Cookie": cookie_header,
            "User-Agent": _UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://chatgpt.com/",
            "Origin": "https://chatgpt.com",
        }
    )
    return session


def get_access_token(session) -> str:
    resp = session.get(SESSION_URL)
    if resp.status_code != 200:
        raise SystemExit(f"Failed to start ChatGPT session ({resp.status_code}). Sign in again.")
    if not resp.text.strip():
        raise SystemExit("Empty session response — the ChatGPT session looks expired. Sign in again.")
    token = resp.json().get("accessToken")
    if not token:
        raise SystemExit("No access token in the ChatGPT session response. Sign in again.")
    return token


def _auth_headers(access_token: str, account_id: str | None) -> dict:
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers


def discover_account_id(session, access_token: str) -> str | None:
    """Best-effort: pick the workspace/account id so listing is scoped correctly."""
    try:
        resp = session.get(ACCOUNTS_URL, headers=_auth_headers(access_token, None))
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None
    accounts = data.get("accounts") or {}
    # Prefer a workspace/business account over the personal one when present.
    best = None
    for key, entry in accounts.items():
        if not isinstance(entry, dict):
            continue
        account = entry.get("account") or {}
        account_id = account.get("account_id") or key
        plan = (account.get("plan_type") or account.get("structure") or "").lower()
        if "workspace" in plan or "business" in plan or "team" in plan or "enterprise" in plan:
            return account_id
        best = best or account_id
    return best


def list_conversations(
    session,
    access_token: str,
    account_id: str | None,
    limit: int | None,
    delay: float,
    page_size: int = 100,
    progress: ProgressFn | None = None,
) -> list[dict]:
    headers = _auth_headers(access_token, account_id)
    all_items: list[dict] = []
    offset = 0
    while True:
        params = {"offset": offset, "limit": page_size, "order": "updated"}
        resp = session.get(f"{BASE_URL}/conversations", headers=headers, params=params)
        if resp.status_code != 200:
            raise SystemExit(f"Failed to list conversations ({resp.status_code}) at offset {offset}.")
        data = resp.json()
        items = data.get("items", [])
        total = data.get("total", 0)
        all_items.extend(items)
        if progress:
            progress(f"Listed {len(all_items)} of {total or '?'} conversations…")
        if limit and len(all_items) >= limit:
            return all_items[:limit]
        if not items or len(all_items) >= total:
            break
        offset += page_size
        if delay:
            time.sleep(delay)
    return all_items


def get_conversation_detail(session, convo_id: str, access_token: str, account_id: str | None) -> dict:
    headers = _auth_headers(access_token, account_id)
    resp = session.get(f"{BASE_URL}/conversation/{convo_id}", headers=headers)
    if resp.status_code != 200:
        return {}
    return resp.json()


def _resolve_cookie_header(options: FetchOptions) -> str:
    if options.session_token:
        return f"__Secure-next-auth.session-token={options.session_token}"
    from .browser_move import AuthOptions, extract_session_cookie

    return extract_session_cookie(
        AuthOptions(
            workspace=options.workspace,
            user_data_dir=options.user_data_dir,
            channel=options.channel,
        )
    )


def run_fetch(options: FetchOptions, progress: ProgressFn | None = None) -> Path:
    """Pull all conversations for the signed-in account into the workspace.

    Returns the raw_chats path, same as file ingest, so classify/build proceed
    unchanged.
    """
    from .core import ProjectChatsError, normalize_chatgpt_export, persist_chats

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    cookie_header = _resolve_cookie_header(options)
    session = _make_session(cookie_header)

    log("Authenticating with ChatGPT…")
    access_token = get_access_token(session)
    account_id = options.account_id or discover_account_id(session, access_token)

    metas = list_conversations(
        session, access_token, account_id, options.limit, options.delay, options.page_size, progress
    )
    if not metas:
        raise ProjectChatsError("No conversations returned. Check that you're signed in to the right workspace.")

    details: list[dict] = []
    for idx, meta in enumerate(metas, start=1):
        convo_id = meta.get("id") or meta.get("conversation_id")
        if not convo_id:
            continue
        log(f"Downloading conversation {idx} of {len(metas)}…")
        detail = get_conversation_detail(session, convo_id, access_token, account_id)
        if not detail:
            continue
        detail.setdefault("id", convo_id)
        detail.setdefault("title", meta.get("title") or "Untitled chat")
        details.append(detail)
        if options.delay:
            time.sleep(options.delay)

    chats = normalize_chatgpt_export(details, "chatgpt-api", options.user_label)
    if not chats:
        raise ProjectChatsError("Fetched conversations but none had readable messages.")
    path = persist_chats(chats, options.workspace, options.user_label)
    log(f"Saved {len(chats)} conversation(s).")
    return path
