from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_WORKSPACE = Path("project-chat-run")


class ProjectChatsError(Exception):
    """User-facing error the CLI prints and the GUI shows in a dialog."""


def default_workspace_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Project Chats"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Project Chats"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "project-chats"


def slugify_project_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-").lower()
    return cleaned or "untitled"


@dataclass
class Message:
    author: str
    text: str


@dataclass
class Chat:
    user_label: str
    conversation_id: str
    title: str
    url: str
    messages: list[Message]
    source_file: str


def workspace_paths(workspace: Path) -> dict[str, Path]:
    return {
        "root": workspace,
        "data": workspace / "data",
        "raw": workspace / "data" / "raw_chats",
        "outputs": workspace / "outputs",
        "profile": workspace / "project_profile.json",
    }


def ensure_workspace(workspace: Path) -> dict[str, Path]:
    paths = workspace_paths(workspace)
    paths["raw"].mkdir(parents=True, exist_ok=True)
    paths["outputs"].mkdir(parents=True, exist_ok=True)
    return paths


def init_workspace(workspace: Path, project_name: str, terms: list[str], force: bool = False) -> Path:
    paths = ensure_workspace(workspace)
    profile_path = paths["profile"]
    if profile_path.exists() and not force:
        raise ProjectChatsError(f"{profile_path} already exists. Use --force to overwrite it.")
    profile = {
        "project_name": project_name,
        "terms": sorted(set(t.strip() for t in terms if t.strip()), key=str.lower),
        "high_confidence_score": 8,
        "possible_score": 3,
    }
    profile_path.write_text(json.dumps(profile, indent=2) + "\n")
    return profile_path


def load_profile(workspace: Path) -> dict:
    profile_path = workspace_paths(workspace)["profile"]
    if not profile_path.exists():
        raise ProjectChatsError(f"Missing {profile_path}. Create a project first.")
    profile = json.loads(profile_path.read_text())
    terms = []
    if isinstance(profile.get("terms"), list):
        terms.extend(profile["terms"])
    for key in ("keywords", "aliases", "people", "repos", "clients", "domains"):
        if isinstance(profile.get(key), list):
            terms.extend(profile[key])
    terms = sorted(set(str(t).strip() for t in terms if str(t).strip()), key=str.lower)
    if not terms:
        raise ProjectChatsError(f"Add search terms to {profile_path}.")
    profile["_terms"] = terms
    profile["_term_patterns"] = [(term, compile_term(term)) for term in terms]
    return profile


def compile_term(term: str) -> re.Pattern:
    escaped = re.escape(term)
    if re.match(r"^[\w.-]+$", term):
        return re.compile(rf"(?<![\w.-]){escaped}(?![\w.-])", re.I)
    return re.compile(escaped, re.I)


def ingest(inputs: list[Path], workspace: Path, user_label: str) -> Path:
    chats: list[Chat] = []
    for input_path in expand_inputs(inputs):
        chats.extend(load_input(input_path, user_label))
    if not chats:
        raise ProjectChatsError("No chats found in the provided input.")
    return persist_chats(chats, workspace, user_label)


def persist_chats(chats: list[Chat], workspace: Path, user_label: str) -> Path:
    """Merge chats into the user's raw_chats file and record which ids are new.

    Shared by file ingest and ChatGPT API fetch so both land in the same place
    and feed the same classify/build stages.
    """
    paths = ensure_workspace(workspace)
    output_path = paths["raw"] / f"{safe_name(user_label)}.json"
    existing = []
    if output_path.exists():
        existing = json.loads(output_path.read_text())
    existing_ids = {str(row.get("conversation_id")) for row in normalize_for_json(existing) if row.get("conversation_id")}
    incoming_ids = {chat.conversation_id for chat in chats}
    new_ids = sorted(incoming_ids - existing_ids)
    merged = normalize_for_json(existing) + [chat_to_json(chat) for chat in chats]
    output_path.write_text(json.dumps(dedupe_chats(merged), indent=2) + "\n")
    new_ids_path = paths["raw"] / f"{safe_name(user_label)}.new_ids.json"
    new_ids_path.write_text(json.dumps(new_ids, indent=2) + "\n")
    return output_path


def load_new_ids(workspace: Path) -> set[str]:
    raw = workspace_paths(workspace)["raw"]
    ids: set[str] = set()
    for path in raw.glob("*.new_ids.json"):
        try:
            entries = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(entries, list):
            ids.update(str(entry) for entry in entries if entry)
    return ids


def expand_inputs(inputs: list[Path]) -> Iterable[Path]:
    for path in inputs:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.suffix.lower() in {".json", ".md", ".txt"}:
                    yield child
        else:
            yield path


def load_input(path: Path, user_label: str) -> list[Chat]:
    if not path.exists():
        raise ProjectChatsError(f"Input not found: {path}")
    if path.suffix.lower() == ".json":
        obj = json.loads(path.read_text())
        parsed = normalize_chatgpt_export(obj, path.name, user_label)
        if not parsed:
            parsed = normalize_custom(obj, path.name, user_label)
        return parsed
    text = path.read_text(errors="replace")
    return [
        Chat(
            user_label=user_label,
            conversation_id=f"{safe_name(user_label)}:{path.stem}",
            title=path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
            url="",
            messages=[Message(author="unknown", text=text.strip())] if text.strip() else [],
            source_file=path.name,
        )
    ]


def text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if "parts" in content:
            return text_from_content(content["parts"])
        return str(content.get("text") or content.get("content") or "")
    return str(content)


def normalize_chatgpt_export(obj, source_file: str, default_user_label: str) -> list[Chat]:
    if isinstance(obj, dict) and "mapping" in obj:
        obj = [obj]
    if not isinstance(obj, list):
        return []
    chats = []
    for idx, conv in enumerate(obj):
        if not isinstance(conv, dict) or not isinstance(conv.get("mapping"), dict):
            continue
        messages = []
        for node in conv["mapping"].values():
            msg = node.get("message") if isinstance(node, dict) else None
            if not isinstance(msg, dict):
                continue
            author = "unknown"
            if isinstance(msg.get("author"), dict):
                author = msg["author"].get("role") or msg["author"].get("name") or "unknown"
            text = text_from_content((msg.get("content") or {}).get("parts"))
            if text.strip():
                messages.append(Message(author=author, text=text.strip()))
        cid = str(conv.get("id") or conv.get("conversation_id") or f"{source_file}:{idx}")
        chats.append(
            Chat(
                user_label=str(conv.get("user_label") or default_user_label),
                conversation_id=cid,
                title=str(conv.get("title") or "Untitled chat"),
                url=str(conv.get("url") or f"https://chatgpt.com/c/{cid}"),
                messages=messages,
                source_file=source_file,
            )
        )
    return chats


def normalize_custom(obj, source_file: str, default_user_label: str) -> list[Chat]:
    rows = obj if isinstance(obj, list) else obj.get("chats", []) if isinstance(obj, dict) else []
    chats = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        messages = []
        for msg in row.get("messages") or []:
            if isinstance(msg, str):
                text = msg
                author = "unknown"
            elif isinstance(msg, dict):
                text = text_from_content(msg.get("text") or msg.get("content"))
                author = str(msg.get("author") or msg.get("role") or "unknown")
            else:
                continue
            if text.strip():
                messages.append(Message(author=author, text=text.strip()))
        cid = str(row.get("conversation_id") or row.get("id") or f"{source_file}:{idx}")
        chats.append(
            Chat(
                user_label=str(row.get("user_label") or default_user_label),
                conversation_id=cid,
                title=str(row.get("title") or "Untitled chat"),
                url=str(row.get("url") or f"https://chatgpt.com/c/{cid}" if cid else ""),
                messages=messages,
                source_file=source_file,
            )
        )
    return chats


def normalize_for_json(rows) -> list[dict]:
    return [row for row in rows if isinstance(row, dict)]


def chat_to_json(chat: Chat) -> dict:
    return {
        "user_label": chat.user_label,
        "conversation_id": chat.conversation_id,
        "title": chat.title,
        "url": chat.url,
        "messages": [{"author": m.author, "text": m.text} for m in chat.messages],
        "source_file": chat.source_file,
    }


def dedupe_chats(rows: list[dict]) -> list[dict]:
    seen = {}
    for row in rows:
        key = row.get("conversation_id") or row.get("url") or row.get("title")
        seen[str(key)] = row
    return list(seen.values())


def load_chats(workspace: Path) -> list[Chat]:
    raw = workspace_paths(workspace)["raw"]
    chats = []
    for path in sorted(raw.glob("*.json")):
        chats.extend(normalize_custom(json.loads(path.read_text()), path.name, path.stem))
    return chats


def classify(workspace: Path) -> Path:
    profile = load_profile(workspace)
    paths = ensure_workspace(workspace)
    chats = load_chats(workspace)
    if not chats:
        raise ProjectChatsError("No ingested chats found. Import a chat file first.")
    rows = []
    for chat in chats:
        result = score_chat(chat, profile)
        if result["label"] == "not_relevant":
            continue
        rows.append(
            {
                "user_label": chat.user_label,
                "conversation_id": chat.conversation_id,
                "title": chat.title,
                "url": chat.url,
                "score": result["score"],
                "label": result["label"],
                "matched_terms": "; ".join(result["matched_terms"]),
                "reason": result["reason"],
                "approved": "true" if result["label"].startswith("high") else "false",
                "move_status": "pending_review",
                "source_file": chat.source_file,
                "snippets": " | ".join(result["snippets"]),
            }
        )
    rows.sort(key=lambda r: (-int(r["score"]), r["user_label"], r["title"].lower()))
    queue = paths["outputs"] / "review_queue.csv"
    write_csv(queue, rows, REVIEW_FIELDS)
    write_review_html(paths["outputs"] / "review_queue.html", rows, profile)
    return queue


def score_chat(chat: Chat, profile: dict) -> dict:
    title = chat.title or ""
    body = "\n".join(m.text for m in chat.messages)
    score = 0
    matched = []
    snippets = []
    for term, pattern in profile["_term_patterns"]:
        title_hits = len(pattern.findall(title))
        body_hits = len(pattern.findall(body))
        if title_hits or body_hits:
            matched.append(term)
            score += title_hits * 4 + min(body_hits, 6)
            snippets.extend(find_snippets(body, pattern, term))
    label = "not_relevant"
    if score >= int(profile.get("high_confidence_score", 8)):
        label = "high_confidence_project_chat"
    elif score >= int(profile.get("possible_score", 3)):
        label = "possible_project_chat"
    return {
        "score": score,
        "label": label,
        "matched_terms": matched,
        "snippets": snippets[:5],
        "reason": make_reason(label, matched),
    }


def find_snippets(text: str, pattern: re.Pattern, term: str) -> list[str]:
    snippets = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 160)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        if snippet:
            snippets.append(f"{term}: {snippet}")
        if len(snippets) >= 2:
            break
    return snippets


def make_reason(label: str, matched: list[str]) -> str:
    if not matched:
        return "No profile terms matched."
    prefix = "High confidence" if label.startswith("high") else "Possible match"
    return f"{prefix}: matched {', '.join(matched[:12])}."


REVIEW_FIELDS = [
    "user_label",
    "conversation_id",
    "title",
    "url",
    "score",
    "label",
    "matched_terms",
    "reason",
    "approved",
    "move_status",
    "source_file",
    "snippets",
]


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_review_rows(workspace: Path) -> list[dict]:
    queue = workspace_paths(workspace)["outputs"] / "review_queue.csv"
    if not queue.exists():
        raise ProjectChatsError("No review queue found. Run classify first.")
    with queue.open() as f:
        return list(csv.DictReader(f))


def build_outputs(workspace: Path) -> list[Path]:
    profile = load_profile(workspace)
    paths = ensure_workspace(workspace)
    chats = {chat.conversation_id: chat for chat in load_chats(workspace)}
    rows = [r for r in read_review_rows(workspace) if str(r.get("approved", "")).lower() == "true"]
    output_paths = []
    output_paths.append(write_source_chats(paths["outputs"], rows))
    output_paths.extend(write_memory_files(paths["outputs"], profile, chats, rows))
    output_paths.append(write_move_queue(paths["outputs"], profile, rows))
    output_paths.append(write_run_report(paths["outputs"], profile, rows))
    return output_paths


def write_source_chats(outputs: Path, rows: list[dict]) -> Path:
    fields = ["user_label", "conversation_id", "title", "url", "score", "matched_terms", "move_status"]
    path = outputs / "source_chats.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    return path


def matching_messages(chat: Chat, profile: dict) -> list[tuple[Message, list[str]]]:
    out = []
    for msg in chat.messages:
        hits = [term for term, pattern in profile["_term_patterns"] if pattern.search(msg.text)]
        if hits:
            out.append((msg, hits))
    return out


def write_memory_files(outputs: Path, profile: dict, chats: dict[str, Chat], rows: list[dict]) -> list[Path]:
    project = profile["project_name"]
    by_user = Counter(r["user_label"] for r in rows)
    match_terms = Counter()
    evidence = []
    for row in rows:
        for term in filter(None, [t.strip() for t in row.get("matched_terms", "").split(";")]):
            match_terms[term] += 1
        chat = chats.get(row["conversation_id"])
        if chat:
            for msg, hits in matching_messages(chat, profile)[:8]:
                evidence.append((row, msg, hits))

    brief = outputs / "project_brief.md"
    brief.write_text(
        f"# {project} ChatGPT Project Brief\n\n"
        f"Generated from {len(rows)} approved candidate chats across {len(by_user)} user label(s).\n\n"
        "## Top Signals\n\n"
        + ("\n".join(f"- {term}: {count} chat(s)" for term, count in match_terms.most_common(25)) or "- No terms matched.")
        + "\n\n## Source Coverage\n\n"
        + ("\n".join(f"- {user}: {count} approved chat(s)" for user, count in by_user.items()) or "- No approved chats.")
        + "\n\n## Evidence Highlights\n\n"
        + format_evidence(evidence[:40])
    )
    decisions = outputs / "decisions.md"
    requirements = outputs / "requirements.md"
    questions = outputs / "open_questions.md"
    decisions.write_text(format_section(project, "Decisions", extract_lines(evidence, ("decision", "decided", "we will", "approved", "final"))))
    requirements.write_text(format_section(project, "Requirements", extract_lines(evidence, ("require", "must", "should", "need", "acceptance", "spec"))))
    questions.write_text(format_section(project, "Open Questions", extract_lines(evidence, ("?", "open question", "todo", "tbd", "unclear", "blocked"))))
    return [brief, decisions, requirements, questions]


def format_evidence(items: list[tuple[dict, Message, list[str]]]) -> str:
    if not items:
        return "- No matching message evidence captured.\n"
    lines = []
    for row, msg, hits in items:
        text = re.sub(r"\s+", " ", msg.text).strip()
        if len(text) > 360:
            text = text[:357] + "..."
        lines.append(f"- `{row['title']}` ({row['user_label']}): {text} [matches: {', '.join(hits[:6])}]")
    return "\n".join(lines) + "\n"


def extract_lines(items: list[tuple[dict, Message, list[str]]], needles: tuple[str, ...]) -> list[str]:
    lines = []
    seen = set()
    for row, msg, _hits in items:
        text = re.sub(r"\s+", " ", msg.text).strip()
        lower = text.lower()
        if any(n in lower for n in needles):
            item = f"- `{row['title']}` ({row['user_label']}): {text[:500]}"
            if item not in seen:
                seen.add(item)
                lines.append(item)
    return lines[:80]


def format_section(project: str, title: str, lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "- No explicit items detected by the first-pass extractor.\n"
    return f"# {project} {title}\n\n{body}\n"


def write_review_html(path: Path, rows: list[dict], profile: dict) -> None:
    table_rows = []
    for row in rows:
        url = html.escape(row.get("url") or "")
        title = html.escape(row.get("title") or "")
        chat_link = f"<a href=\"{url}\">{title}</a>" if url else title
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(row.get('approved', ''))}</td>"
            f"<td>{html.escape(row.get('user_label', ''))}</td>"
            f"<td>{html.escape(str(row.get('score', '')))}</td>"
            f"<td>{chat_link}</td>"
            f"<td>{html.escape(row.get('label', ''))}</td>"
            f"<td>{html.escape(row.get('matched_terms', ''))}</td>"
            f"<td>{html.escape(row.get('reason', ''))}</td>"
            f"<td>{html.escape(row.get('snippets', ''))}</td>"
            "</tr>"
        )
    write_html_page(
        path,
        f"{profile['project_name']} review queue",
        "<p>Edit <code>review_queue.csv</code>. Keep <code>approved=true</code> only for chats to move or summarize.</p>"
        + table(
            ["Approved", "User", "Score", "Chat", "Label", "Matches", "Reason", "Snippets"],
            table_rows,
        ),
    )


def write_move_queue(outputs: Path, profile: dict, rows: list[dict]) -> Path:
    path = outputs / "move_queue.html"
    cards = []
    for idx, row in enumerate(rows, 1):
        url = html.escape(row.get("url") or "")
        title = html.escape(row.get("title") or "Untitled chat")
        link = f"<a href=\"{url}\">{title}</a>" if url else title
        cards.append(
            f"<section><h2>{idx}. {link}</h2>"
            f"<p><b>User:</b> {html.escape(row.get('user_label', ''))} "
            f"<b>Score:</b> {html.escape(str(row.get('score', '')))}</p>"
            f"<p>{html.escape(row.get('reason', ''))}</p>"
            "<ol><li>Open the chat link while signed into the owning ChatGPT account.</li>"
            f"<li>Use the chat menu to move it to project <b>{html.escape(profile['project_name'])}</b>.</li>"
            "<li>Mark the row moved in your tracking sheet or move log.</li></ol></section>"
        )
    write_html_page(
        path,
        f"{profile['project_name']} move queue",
        "<p>Move only the approved chats below. ChatGPT Project movement is a product UI action.</p>"
        + ("\n".join(cards) if cards else "<p>No approved chats.</p>"),
    )
    return path


def write_run_report(outputs: Path, profile: dict, rows: list[dict]) -> Path:
    path = outputs / "run_report.md"
    by_user = Counter(r["user_label"] for r in rows)
    path.write_text(
        f"# {profile['project_name']} Run Report\n\n"
        f"- Generated: {datetime.now(timezone.utc).isoformat()}\n"
        f"- Approved chats: {len(rows)}\n"
        f"- User labels: {', '.join(sorted(by_user)) or 'none'}\n"
        "- Review queue: `review_queue.csv`\n"
        "- Move queue: `move_queue.html`\n"
    )
    return path


def table(headers: list[str], rows: list[str]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def write_html_page(path: Path, title: str, body: str) -> None:
    path.write_text(
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;line-height:1.4}"
        "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ddd;padding:8px;vertical-align:top}"
        "th{background:#f5f5f5;text-align:left}tr:nth-child(even){background:#fafafa}"
        "section{border:1px solid #ddd;border-radius:8px;padding:16px;margin:16px 0}code{background:#f5f5f5;padding:2px 4px;border-radius:4px}</style>"
        f"<h1>{html.escape(title)}</h1>{body}"
    )


def bundle(workspace: Path) -> Path:
    paths = ensure_workspace(workspace)
    outputs = paths["outputs"]
    if not outputs.exists():
        raise ProjectChatsError("No outputs found. Build the outputs first.")
    zip_path = workspace / "project-chat-handoff.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(paths["profile"], paths["profile"].relative_to(workspace))
        for path in sorted(outputs.glob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(workspace))
    return zip_path


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned or "user"


def copy_example(destination: Path) -> None:
    if destination.exists():
        raise ProjectChatsError(f"{destination} already exists.")
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples", destination)
