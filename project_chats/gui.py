from __future__ import annotations

import csv
import importlib.util
import json
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from .core import (
    DEFAULT_WORKSPACE,
    REVIEW_FIELDS,
    default_workspace_root,
    load_new_ids,
    slugify_project_name,
    workspace_paths,
    write_csv,
)


CHECK_ON = "☑"
CHECK_OFF = "☐"


def build_cli_command(workspace: Path, args: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "project_chats",
        "--workspace",
        str(workspace),
        *args,
    ]


def load_tkinter() -> None:
    global tk, ttk, filedialog, messagebox

    try:
        import tkinter as tk_mod
        from tkinter import ttk as ttk_mod
        from tkinter import filedialog as fd
        from tkinter import messagebox as mb
        tk = tk_mod
        ttk = ttk_mod
        filedialog = fd
        messagebox = mb
    except ImportError as exc:
        _tk_missing_exit(exc)


def _tk_missing_exit(exc: Exception) -> None:
    msg = "The desktop GUI requires Python Tkinter, which is missing from this Python build.\n\n"

    if sys.platform == "darwin":
        # Detect homebrew Python by checking sys.executable path
        exe = sys.executable.lower()
        if "homebrew" in exe or "cellar" in exe or "opt" in exe:
            # Try to figure out the version suffix
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            msg += (
                f"You are using Homebrew Python, which does not include Tkinter by default.\n"
                f"Fix it with:\n\n"
                f"    brew install python-tk@{ver}\n\n"
                f"Then reinstall the app:\n\n"
                f"    pipx reinstall project-chats\n\n"
                f"Or install the official Python from https://python.org, which includes Tkinter."
            )
        else:
            msg += "Install the official Python from https://python.org (it includes Tkinter)."
    elif sys.platform.startswith("linux"):
        msg += (
            "Install Tkinter for your distribution, then reinstall:\n\n"
            "  Ubuntu/Debian:  sudo apt install python3-tk\n"
            "  Fedora:         sudo dnf install python3-tkinter\n"
            "  Arch:           sudo pacman -S tk\n\n"
            "Then: pipx reinstall project-chats"
        )
    else:
        msg += "Install a Python build that includes Tkinter (e.g. the official python.org installer)."

    msg += "\n\nAlternatively, use the CLI: project-chats --help"
    raise SystemExit(msg) from exc


STEP_TITLES = [
    "1. New Project",
    "2. Import Chats",
    "3. Review",
    "4. Move",
]


class ProjectChatsApp:
    def __init__(self, root) -> None:
        self.root = root
        self.root.title("Project Chats")
        self.root.geometry("1000x720")
        self.root.minsize(880, 560)

        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.running = False
        self.last_exit_code: int | None = None
        self.playwright_ok = False
        self._quiet_fail = False

        self.workspace_path = self._resolve_initial_workspace()
        self.current_step = 1

        self.project_var = tk.StringVar()
        self.terms_var = tk.StringVar()
        self.user_label_var = tk.StringVar()
        self.input_path_var = tk.StringVar()
        self.browser_profile_var = tk.StringVar()
        self.browser_channel_var = tk.StringVar(value="chrome")
        self.move_project_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=True)
        self.headless_var = tk.BooleanVar(value=False)
        self.limit_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.workspace_label_var = tk.StringVar()
        self.show_new_only_var = tk.BooleanVar(value=False)
        self.auth_status_var = tk.StringVar(value="ChatGPT sign-in status unknown.")

        self.review_rows: list[dict] = []
        self.new_ids: set[str] = set()
        self.row_lookup: dict[str, dict] = {}

        self._build_style()
        self._build_menu()
        self._build_layout()

        self._refresh_workspace_label()
        self._populate_from_profile_if_present()
        self._goto_step(self._first_uncompleted_step())

        self.root.after(100, self._drain_output_queue)
        self.root.after(300, self._bootstrap_playwright)

    # ------------------------------------------------------------------ Style

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Step.TLabel", padding=(14, 10), font=("TkDefaultFont", 11))
        style.configure("StepActive.TLabel", padding=(14, 10), font=("TkDefaultFont", 11, "bold"), background="#dbeafe")
        style.configure("StepDone.TLabel", padding=(14, 10), font=("TkDefaultFont", 11), foreground="#15803d")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Status.TLabel", padding=(10, 4), background="#f3f4f6")
        style.configure("Rail.TFrame", background="#f9fafb")
        style.configure("NEW.TLabel", foreground="#1d4ed8", font=("TkDefaultFont", 9, "bold"))

    # ------------------------------------------------------------------ Menu

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        settings = tk.Menu(menubar, tearoff=False)
        settings.add_command(label="Workspace…", command=self._choose_workspace)
        settings.add_command(label="Show Workspace Folder", command=self._show_workspace)
        settings.add_command(label="Open Profile JSON", command=lambda: self._open_path("project_profile.json"))
        settings.add_separator()
        settings.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Settings", menu=settings)
        self.root.config(menu=menubar)

    # ------------------------------------------------------------------ Layout

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # Header strip with workspace path (small, dimmed)
        header = ttk.Frame(outer)
        header.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(header, textvariable=self.workspace_label_var, foreground="#6b7280").pack(side="left")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, padx=12, pady=8)

        rail = ttk.Frame(body, style="Rail.TFrame", width=200)
        rail.pack(side="left", fill="y", padx=(0, 12))
        rail.pack_propagate(False)
        self.step_labels: list[ttk.Label] = []
        for idx, title in enumerate(STEP_TITLES, start=1):
            label = ttk.Label(rail, text=title, style="Step.TLabel", anchor="w")
            label.pack(fill="x", pady=(8 if idx == 1 else 2, 2), padx=4)
            label.bind("<Button-1>", lambda _evt, step=idx: self._goto_step(step))
            self.step_labels.append(label)

        self.step_container = ttk.Frame(body)
        self.step_container.pack(side="left", fill="both", expand=True)

        # Status bar
        status_frame = ttk.Frame(outer)
        status_frame.pack(fill="x", side="bottom")
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(fill="x")

        # Collapsible log
        self.log_visible = False
        self.log_frame = ttk.Frame(outer)
        self.log_toggle = ttk.Button(outer, text="▸ Show command log", command=self._toggle_log)
        self.log_toggle.pack(fill="x", side="bottom", padx=12, pady=(0, 4))

        log_inner = ttk.LabelFrame(self.log_frame, text="Command Log")
        log_inner.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.log = tk.Text(log_inner, height=10, wrap="word")
        self.log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        log_scroll = ttk.Scrollbar(log_inner, orient="vertical", command=self.log.yview)
        log_scroll.pack(side="right", fill="y", pady=6)
        self.log.config(yscrollcommand=log_scroll.set)
        clear = ttk.Button(log_inner, text="Clear", command=lambda: self.log.delete("1.0", "end"))
        clear.pack(side="right", anchor="n", padx=6, pady=6)

    def _toggle_log(self, *, show: bool | None = None) -> None:
        target = (not self.log_visible) if show is None else show
        if target == self.log_visible:
            return
        if target:
            self.log_frame.pack(fill="both", expand=False, side="bottom", before=self.log_toggle)
            self.log_toggle.config(text="▾ Hide command log")
        else:
            self.log_frame.pack_forget()
            self.log_toggle.config(text="▸ Show command log")
        self.log_visible = target

    # ------------------------------------------------------------------ Step navigation

    def _goto_step(self, step: int) -> None:
        step = max(1, min(4, step))
        if step >= 3 and not self._has_profile():
            self._set_status("Create a project first.")
            return
        if step == 3 and not self._has_review_queue():
            self._set_status("Import chats and run classify before reviewing.")
            return
        self.current_step = step
        for idx, label in enumerate(self.step_labels, start=1):
            if idx == step:
                label.configure(style="StepActive.TLabel")
            elif self._is_step_done(idx):
                label.configure(style="StepDone.TLabel", text=f"✓ {STEP_TITLES[idx-1].split('. ', 1)[1]}")
            else:
                label.configure(style="Step.TLabel", text=STEP_TITLES[idx-1])
        self._render_current_step()

    def _render_current_step(self) -> None:
        for child in self.step_container.winfo_children():
            child.destroy()
        if self.current_step == 1:
            self._render_step_new_project(self.step_container)
        elif self.current_step == 2:
            self._render_step_import(self.step_container)
        elif self.current_step == 3:
            self._render_step_review(self.step_container)
        else:
            self._render_step_move(self.step_container)

    def _first_uncompleted_step(self) -> int:
        if not self._has_profile():
            return 1
        if not self._has_ingested_chats():
            return 2
        if not self._has_review_queue():
            return 2
        if not self._has_built_outputs():
            return 3
        return 4

    def _is_step_done(self, step: int) -> bool:
        if step == 1:
            return self._has_profile()
        if step == 2:
            return self._has_ingested_chats() and self._has_review_queue()
        if step == 3:
            return self._has_built_outputs()
        return False

    # ------------------------------------------------------------------ Step 1: New Project

    def _render_step_new_project(self, parent) -> None:
        card = ttk.Frame(parent, padding=16)
        card.pack(fill="both", expand=True)

        ttk.Label(card, text="Start a new project", font=("TkDefaultFont", 16, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            card,
            text="Project Chats finds and groups ChatGPT conversations that belong to a project. Give your project a name and a list of terms that mark a chat as relevant.",
            wraplength=720,
            justify="left",
            foreground="#374151",
        ).pack(anchor="w", pady=(0, 16))

        ttk.Label(card, text="Project name").pack(anchor="w")
        ttk.Entry(card, textvariable=self.project_var, width=60).pack(anchor="w", fill="x", pady=(2, 12))

        ttk.Label(card, text="Terms (comma separated)").pack(anchor="w")
        ttk.Entry(card, textvariable=self.terms_var, width=60).pack(anchor="w", fill="x", pady=(2, 6))
        ttk.Label(
            card,
            text="Project names, aliases, people, repos, clients, domains — anything that identifies a chat about this project.",
            wraplength=720,
            justify="left",
            foreground="#6b7280",
        ).pack(anchor="w", pady=(0, 16))

        action = "Update Project" if self._has_profile() else "Create Project"
        ttk.Button(card, text=action, command=self._create_project).pack(anchor="w")

    def _create_project(self) -> None:
        name = self.project_var.get().strip()
        terms = [t.strip() for t in self.terms_var.get().split(",") if t.strip()]
        if not name:
            messagebox.showerror("Missing project name", "Enter a project name.")
            return
        if not terms:
            messagebox.showerror("Missing terms", "Enter at least one term.")
            return
        # Move workspace to a slug-based path if it is still pointing at the default "untitled".
        new_workspace = self._reslot_workspace_for_project(name)
        args = ["init", "--project-name", name, "--force"]
        for term in terms:
            args.extend(["--term", term])
        self._run_cli(args, workspace_override=new_workspace, on_done=self._after_create_project)

    def _after_create_project(self, code: int) -> None:
        if code != 0:
            return
        self._refresh_workspace_label()
        self._goto_step(2)

    def _reslot_workspace_for_project(self, name: str) -> Path:
        slug = slugify_project_name(name)
        root = default_workspace_root()
        current_name = self.workspace_path.name
        if current_name in ("untitled", "project-chat-run") and not self._has_profile():
            new_path = root / slug
            new_path.mkdir(parents=True, exist_ok=True)
            self.workspace_path = new_path
            return new_path
        return self.workspace_path

    # ------------------------------------------------------------------ Step 2: Import

    def _render_step_import(self, parent) -> None:
        card = ttk.Frame(parent, padding=16)
        card.pack(fill="both", expand=True)

        ttk.Label(card, text="Import chats", font=("TkDefaultFont", 16, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            card,
            text="Pick a ChatGPT export (conversations.json), a normalized JSON file, a folder of Markdown/text, or any combination. You can import more later — only new chats are shown in review.",
            wraplength=720,
            justify="left",
            foreground="#374151",
        ).pack(anchor="w", pady=(0, 16))

        fetch_box = ttk.LabelFrame(card, text="No export? Fetch from ChatGPT (Team/Business)", padding=10)
        fetch_box.pack(fill="x", pady=(0, 16))
        ttk.Label(
            fetch_box,
            text="Team/Business accounts can't export chats. This downloads your own conversations directly through ChatGPT — sign in first on the Move step, then fetch here. Set your label below first.",
            wraplength=680,
            justify="left",
            foreground="#374151",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(fetch_box, text="Fetch my ChatGPT chats", command=self._fetch_from_chatgpt).pack(anchor="w")

        ttk.Label(card, text="Your label for these chats (e.g. your name)").pack(anchor="w")
        ttk.Entry(card, textvariable=self.user_label_var, width=40).pack(anchor="w", fill="x", pady=(2, 12))

        ttk.Label(card, text="File or folder").pack(anchor="w")
        row = ttk.Frame(card)
        row.pack(fill="x", pady=(2, 12))
        ttk.Entry(row, textvariable=self.input_path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="File…", command=self._choose_input_file).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Folder…", command=self._choose_input_folder).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(card)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Import & Classify", command=self._import_and_classify).pack(side="left")
        ttk.Button(actions, text="Re-classify only", command=self._classify_only).pack(side="left", padx=(8, 0))

    def _choose_input_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose chat input",
            filetypes=[("Supported files", "*.json *.md *.txt"), ("All files", "*.*")],
        )
        if selected:
            self.input_path_var.set(selected)

    def _choose_input_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose input folder")
        if selected:
            self.input_path_var.set(selected)

    def _import_and_classify(self) -> None:
        user_label = self.user_label_var.get().strip()
        input_path = self.input_path_var.get().strip()
        if not user_label:
            messagebox.showerror("Missing label", "Enter a label for these chats (your name works).")
            return
        if not input_path:
            messagebox.showerror("Missing input", "Choose a file or folder to import.")
            return
        args = ["ingest", input_path, "--user-label", user_label]
        self._run_cli(args, on_done=self._after_ingest)

    def _fetch_from_chatgpt(self) -> None:
        user_label = self.user_label_var.get().strip()
        if not user_label:
            messagebox.showerror("Missing label", "Enter a label for these chats (your name works) before fetching.")
            return
        if not self._require_playwright():
            return
        args = ["fetch", "--user-label", user_label]
        if self.browser_profile_var.get().strip():
            args.extend(["--user-data-dir", self.browser_profile_var.get().strip()])
        if self.browser_channel_var.get().strip():
            args.extend(["--channel", self.browser_channel_var.get().strip()])
        self._set_status("Fetching chats from ChatGPT…")
        self._run_cli(args, on_done=self._after_ingest)

    def _classify_only(self) -> None:
        if not self._has_ingested_chats():
            messagebox.showerror("Nothing to classify", "Import a chat file first.")
            return
        self._run_cli(["classify"], on_done=self._after_classify)

    def _after_ingest(self, code: int) -> None:
        if code != 0:
            return
        new = load_new_ids(self.workspace_path)
        self._set_status(f"Imported. {len(new)} new chat(s) since last import." if new else "Imported.")
        self._run_cli(["classify"], on_done=self._after_classify)

    def _after_classify(self, code: int) -> None:
        if code != 0:
            return
        self._goto_step(3)

    # ------------------------------------------------------------------ Step 3: Review

    def _render_step_review(self, parent) -> None:
        outer = ttk.Frame(parent, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Review candidate chats", font=("TkDefaultFont", 16, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            outer,
            text="High-confidence matches are pre-checked. Toggle the box (or press space) to include or exclude a chat. Then Save & Continue.",
            wraplength=720,
            justify="left",
            foreground="#374151",
        ).pack(anchor="w", pady=(0, 12))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Approve all high-confidence", command=lambda: self._bulk_set(lambda r: r.get("label", "").startswith("high"), True)).pack(side="left")
        ttk.Button(toolbar, text="Reject possible", command=lambda: self._bulk_set(lambda r: r.get("label", "") == "possible_project_chat", False)).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Clear all", command=lambda: self._bulk_set(lambda r: True, False)).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(toolbar, text="Show only new chats", variable=self.show_new_only_var, command=self._populate_review_table).pack(side="left", padx=(16, 0))

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)

        columns = ("check", "score", "title", "user", "snippet")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended", height=14)
        self.tree.heading("check", text="✓")
        self.tree.heading("score", text="Score")
        self.tree.heading("title", text="Title")
        self.tree.heading("user", text="User")
        self.tree.heading("snippet", text="Snippet")
        self.tree.column("check", width=40, anchor="center", stretch=False)
        self.tree.column("score", width=60, anchor="center", stretch=False)
        self.tree.column("title", width=280, anchor="w")
        self.tree.column("user", width=100, anchor="w", stretch=False)
        self.tree.column("snippet", width=400, anchor="w")
        self.tree.tag_configure("new", foreground="#1d4ed8")

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.config(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        detail = ttk.LabelFrame(outer, text="Details", padding=8)
        detail.pack(fill="x", pady=(10, 8))
        self.detail_text = tk.Text(detail, height=5, wrap="word")
        self.detail_text.pack(fill="x")
        self.detail_text.config(state="disabled")

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(4, 0))
        self.review_count_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.review_count_var).pack(side="left")
        ttk.Button(bottom, text="Save & Continue", command=self._save_and_build).pack(side="right")
        ttk.Button(bottom, text="Open Detail Link", command=self._open_selected_url).pack(side="right", padx=(0, 8))

        self._load_review_data()
        self._populate_review_table()

    def _load_review_data(self) -> None:
        queue_path = workspace_paths(self.workspace_path)["outputs"] / "review_queue.csv"
        rows: list[dict] = []
        if queue_path.exists():
            with queue_path.open() as f:
                rows = list(csv.DictReader(f))
        self.review_rows = rows
        self.new_ids = load_new_ids(self.workspace_path)

    def _populate_review_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.row_lookup.clear()
        show_new_only = self.show_new_only_var.get()
        approved_count = 0
        total = 0
        for row in self.review_rows:
            is_new = row.get("conversation_id", "") in self.new_ids
            if show_new_only and not is_new:
                continue
            total += 1
            approved = str(row.get("approved", "")).lower() == "true"
            if approved:
                approved_count += 1
            title = row.get("title", "")
            display_title = f"✨ NEW · {title}" if is_new else title
            snippet = (row.get("snippets", "") or "").split(" | ", 1)[0]
            if len(snippet) > 120:
                snippet = snippet[:117] + "…"
            values = (
                CHECK_ON if approved else CHECK_OFF,
                row.get("score", ""),
                display_title,
                row.get("user_label", ""),
                snippet,
            )
            tags = ("new",) if is_new else ()
            item_id = self.tree.insert("", "end", values=values, tags=tags)
            self.row_lookup[item_id] = row
        self.review_count_var.set(f"{approved_count} approved of {total} shown")

    def _on_tree_click(self, event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col == "#1":
            item = self.tree.identify_row(event.y)
            if item:
                self._toggle_item(item)

    def _on_tree_space(self, _event) -> str:
        for item in self.tree.selection():
            self._toggle_item(item)
        return "break"

    def _on_tree_select(self, _event) -> None:
        items = self.tree.selection()
        if not items:
            return
        row = self.row_lookup.get(items[0])
        if not row:
            return
        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", "end")
        bits = [
            f"Title: {row.get('title', '')}",
            f"User: {row.get('user_label', '')}    Score: {row.get('score', '')}    Label: {row.get('label', '')}",
            f"Matched terms: {row.get('matched_terms', '')}",
            f"Reason: {row.get('reason', '')}",
            f"URL: {row.get('url', '')}",
            "",
            "Snippets:",
            (row.get("snippets", "") or "").replace(" | ", "\n  - "),
        ]
        self.detail_text.insert("end", "\n".join(bits))
        self.detail_text.config(state="disabled")

    def _toggle_item(self, item: str) -> None:
        row = self.row_lookup.get(item)
        if not row:
            return
        current = str(row.get("approved", "")).lower() == "true"
        new_value = not current
        row["approved"] = "true" if new_value else "false"
        values = list(self.tree.item(item, "values"))
        values[0] = CHECK_ON if new_value else CHECK_OFF
        self.tree.item(item, values=values)
        self._refresh_review_count()

    def _bulk_set(self, predicate, value: bool) -> None:
        for item, row in self.row_lookup.items():
            if not predicate(row):
                continue
            row["approved"] = "true" if value else "false"
            values = list(self.tree.item(item, "values"))
            values[0] = CHECK_ON if value else CHECK_OFF
            self.tree.item(item, values=values)
        self._refresh_review_count()

    def _refresh_review_count(self) -> None:
        approved = sum(1 for r in self.row_lookup.values() if str(r.get("approved", "")).lower() == "true")
        self.review_count_var.set(f"{approved} approved of {len(self.row_lookup)} shown")

    def _open_selected_url(self) -> None:
        items = self.tree.selection()
        if not items:
            return
        row = self.row_lookup.get(items[0])
        if row and row.get("url"):
            webbrowser.open(row["url"])

    def _save_and_build(self) -> None:
        queue_path = workspace_paths(self.workspace_path)["outputs"] / "review_queue.csv"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(queue_path, self.review_rows, REVIEW_FIELDS)
        self._set_status("Saved review approvals. Building outputs…")
        self._run_cli(["build"], on_done=self._after_build)

    def _after_build(self, code: int) -> None:
        if code != 0:
            return
        self._goto_step(4)

    # ------------------------------------------------------------------ Step 4: Move

    def _render_step_move(self, parent) -> None:
        outer = ttk.Frame(parent, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Move approved chats", font=("TkDefaultFont", 16, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            outer,
            text="Open the move queue to do it by hand, or let Auto-Move drive ChatGPT in a real browser. The browser uses a separate profile — sign in there the first time.",
            wraplength=720,
            justify="left",
            foreground="#374151",
        ).pack(anchor="w", pady=(0, 12))

        self._render_account_box(outer)

        outputs_box = ttk.LabelFrame(outer, text="Generated files", padding=10)
        outputs_box.pack(fill="x", pady=(0, 12))
        for label, rel_path in [
            ("Move Queue (HTML)", "outputs/move_queue.html"),
            ("Project Brief", "outputs/project_brief.md"),
            ("Decisions", "outputs/decisions.md"),
            ("Requirements", "outputs/requirements.md"),
            ("Open Questions", "outputs/open_questions.md"),
            ("Source Chats CSV", "outputs/source_chats.csv"),
            ("Review Queue (HTML)", "outputs/review_queue.html"),
        ]:
            row = ttk.Frame(outputs_box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=24, anchor="w").pack(side="left")
            ttk.Button(row, text="Open", command=lambda p=rel_path: self._open_path(p)).pack(side="left")

        action_row = ttk.Frame(outer)
        action_row.pack(fill="x", pady=(0, 12))
        ttk.Button(action_row, text="Bundle handoff zip", command=lambda: self._run_cli(["bundle"])).pack(side="left")
        ttk.Button(action_row, text="Open handoff zip", command=lambda: self._open_path("project-chat-handoff.zip")).pack(side="left", padx=(8, 0))

        move_box = ttk.LabelFrame(outer, text="Auto-Move via ChatGPT UI", padding=10)
        move_box.pack(fill="x")

        if not self.playwright_ok:
            banner = ttk.Frame(move_box)
            banner.pack(fill="x", pady=(0, 8))
            ttk.Label(
                banner,
                text="Browser automation isn't installed. Auto-Move needs Playwright + Chromium.",
                foreground="#b45309",
            ).pack(side="left")
            ttk.Button(banner, text="Install now", command=self._install_playwright).pack(side="right")

        # Gridded options live in their own frame so move_box stays pack-only
        # (mixing pack and grid in one container is invalid in Tk).
        grid_frame = ttk.Frame(move_box)
        grid_frame.pack(fill="x")

        ttk.Label(grid_frame, text="Project override (optional)").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid_frame, textvariable=self.move_project_var).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)

        ttk.Label(grid_frame, text="Browser profile directory").grid(row=1, column=0, sticky="w")
        prof_row = ttk.Frame(grid_frame)
        prof_row.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2)
        ttk.Entry(prof_row, textvariable=self.browser_profile_var).pack(side="left", fill="x", expand=True)
        ttk.Button(prof_row, text="Choose…", command=self._choose_browser_profile).pack(side="left", padx=(8, 0))

        opts_row = ttk.Frame(grid_frame)
        opts_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(opts_row, text="Browser channel").pack(side="left")
        ttk.Entry(opts_row, textvariable=self.browser_channel_var, width=10).pack(side="left", padx=(4, 12))
        ttk.Checkbutton(opts_row, text="Dry run (preview)", variable=self.dry_run_var).pack(side="left")
        ttk.Checkbutton(opts_row, text="Headless", variable=self.headless_var).pack(side="left", padx=(8, 0))
        ttk.Label(opts_row, text="Limit").pack(side="left", padx=(12, 4))
        ttk.Entry(opts_row, textvariable=self.limit_var, width=6).pack(side="left")

        ttk.Button(grid_frame, text="Auto-Move", command=self._auto_move).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        grid_frame.columnconfigure(1, weight=1)

    def _render_account_box(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="ChatGPT account", padding=10)
        box.pack(fill="x", pady=(0, 12))
        ttk.Label(box, textvariable=self.auth_status_var, foreground="#374151").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="Sign in to ChatGPT", command=self._sign_in_chatgpt).pack(side="left")
        ttk.Button(row, text="Check sign-in", command=self._check_sign_in).pack(side="left", padx=(8, 0))
        ttk.Label(
            box,
            text="Opens your browser using the profile below. Sign in once, then close that browser window — the session is reused for Auto-Move.",
            wraplength=680,
            justify="left",
            foreground="#6b7280",
        ).pack(anchor="w", pady=(6, 0))

    def _login_args(self, extra: list[str]) -> list[str]:
        args = ["login", *extra]
        if self.browser_profile_var.get().strip():
            args.extend(["--user-data-dir", self.browser_profile_var.get().strip()])
        if self.browser_channel_var.get().strip():
            args.extend(["--channel", self.browser_channel_var.get().strip()])
        return args

    def _require_playwright(self) -> bool:
        if self.playwright_ok:
            return True
        messagebox.showerror(
            "Browser automation not installed",
            "Sign-in needs Playwright + Chromium. Install them from the Auto-Move banner below first.",
        )
        return False

    def _sign_in_chatgpt(self) -> None:
        if not self._require_playwright():
            return
        self.auth_status_var.set("Opening your browser — sign in to ChatGPT there, then close that window…")
        self._run_cli(self._login_args([]), on_done=self._after_sign_in)

    def _after_sign_in(self, code: int) -> None:
        if code == 0:
            self.auth_status_var.set("✓ Signed in to ChatGPT.")
        else:
            self.auth_status_var.set("Sign-in not detected — try again.")

    def _check_sign_in(self) -> None:
        if not self._require_playwright():
            return
        self.auth_status_var.set("Checking sign-in…")
        self._run_cli(self._login_args(["--check"]), on_done=self._after_check_sign_in, quiet_fail=True)

    def _after_check_sign_in(self, code: int) -> None:
        if code == 0:
            self.auth_status_var.set("✓ Signed in to ChatGPT.")
        else:
            self.auth_status_var.set("Not signed in. Use “Sign in to ChatGPT”.")

    def _choose_browser_profile(self) -> None:
        selected = filedialog.askdirectory(title="Choose browser profile directory")
        if selected:
            self.browser_profile_var.set(selected)

    def _auto_move(self) -> None:
        if not self.playwright_ok and not self.dry_run_var.get():
            messagebox.showerror(
                "Browser automation not installed",
                "Install Playwright + Chromium from the banner above before running Auto-Move for real.",
            )
            return
        args = ["auto-move"]
        if self.user_label_var.get().strip():
            args.extend(["--user-label", self.user_label_var.get().strip()])
        if self.move_project_var.get().strip():
            args.extend(["--project-name", self.move_project_var.get().strip()])
        if self.browser_profile_var.get().strip():
            args.extend(["--user-data-dir", self.browser_profile_var.get().strip()])
        if self.browser_channel_var.get().strip():
            args.extend(["--channel", self.browser_channel_var.get().strip()])
        if self.dry_run_var.get():
            args.append("--dry-run")
        if self.headless_var.get():
            args.append("--headless")
        if self.limit_var.get().strip():
            args.extend(["--limit", self.limit_var.get().strip()])
        self._run_cli(args)

    # ------------------------------------------------------------------ Subprocess runner

    def _run_cli(self, args: list[str], workspace_override: Path | None = None, on_done=None, quiet_fail: bool = False) -> None:
        if self.running:
            messagebox.showinfo("Busy", "Wait for the current command to finish.")
            return
        workspace = workspace_override or self.workspace_path
        command = build_cli_command(workspace, args)
        self.running = True
        self.last_exit_code = None
        self._quiet_fail = quiet_fail
        action = args[0] if args else "command"
        self._set_status(f"Running {action}…")
        self.output_queue.put(("log", f"\n$ {' '.join(command)}\n"))
        thread = threading.Thread(target=self._run_command_thread, args=(command, on_done), daemon=True)
        thread.start()

    def _run_command_thread(self, command: list[str], on_done) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                self.output_queue.put(("log", line))
                stripped = line.strip()
                if stripped:
                    self.output_queue.put(("status", stripped[:140]))
            code = process.wait()
            self.output_queue.put(("log", f"Command exited with code {code}\n"))
            self.output_queue.put(("done", str(code)))
        except Exception as exc:
            self.output_queue.put(("log", f"Error: {exc}\n"))
            self.output_queue.put(("done", "1"))
        finally:
            self.running = False
            if on_done is not None:
                self.output_queue.put(("callback", on_done))

    def _drain_output_queue(self) -> None:
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "log":
                    self.log.insert("end", payload)
                    self.log.see("end")
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    code = int(payload)
                    self.last_exit_code = code
                    if code != 0 and not self._quiet_fail:
                        self._toggle_log(show=True)
                        last_line = self._last_meaningful_log_line()
                        messagebox.showerror("Command failed", last_line or f"Exit code {code}.")
                        self._set_status(f"Failed: {last_line[:120]}" if last_line else "Failed.")
                    elif code == 0:
                        self._set_status("Done.")
                elif kind == "callback":
                    code = self.last_exit_code if self.last_exit_code is not None else 0
                    try:
                        payload(code)
                    except Exception as exc:
                        self._append_log(f"Callback error: {exc}\n")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_output_queue)

    def _last_meaningful_log_line(self) -> str:
        text = self.log.get("1.0", "end")
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Command exited"):
                continue
            return stripped
        return ""

    def _append_log(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    # ------------------------------------------------------------------ Workspace helpers

    def _resolve_initial_workspace(self) -> Path:
        root = default_workspace_root()
        root.mkdir(parents=True, exist_ok=True)
        # Pick the most-recently-touched project subdir, else "untitled".
        candidates = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        path = root / "untitled"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _choose_workspace(self) -> None:
        selected = filedialog.askdirectory(title="Choose workspace", initialdir=str(self.workspace_path))
        if not selected:
            return
        self.workspace_path = Path(selected)
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self._refresh_workspace_label()
        self._populate_from_profile_if_present()
        self._goto_step(self._first_uncompleted_step())

    def _refresh_workspace_label(self) -> None:
        self.workspace_label_var.set(f"Workspace: {self.workspace_path}")

    def _show_workspace(self) -> None:
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self._reveal(self.workspace_path)

    def _reveal(self, path: Path) -> None:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(path)])
        else:
            webbrowser.open(path.resolve().as_uri())

    def _open_path(self, rel_path: str) -> None:
        path = self.workspace_path / rel_path
        if not path.exists():
            messagebox.showerror("Missing file", f"{path} doesn't exist yet.")
            return
        webbrowser.open(path.resolve().as_uri())

    def _show_about(self) -> None:
        messagebox.showinfo(
            "Project Chats",
            "Project Chats — find, review, and move ChatGPT conversations into a Project.\n\nhttps://github.com/michaeljeisner/project-chats",
        )

    # ------------------------------------------------------------------ Profile

    def _populate_from_profile_if_present(self) -> None:
        profile_path = workspace_paths(self.workspace_path)["profile"]
        if not profile_path.exists():
            self.project_var.set("")
            self.terms_var.set("")
            return
        try:
            profile = json.loads(profile_path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self.project_var.set(str(profile.get("project_name", "")))
        terms = profile.get("terms") or []
        if isinstance(terms, list):
            self.terms_var.set(", ".join(str(t) for t in terms))

    def _has_profile(self) -> bool:
        return workspace_paths(self.workspace_path)["profile"].exists()

    def _has_ingested_chats(self) -> bool:
        raw = workspace_paths(self.workspace_path)["raw"]
        return raw.exists() and any(p.name != "" and not p.name.endswith(".new_ids.json") for p in raw.glob("*.json"))

    def _has_review_queue(self) -> bool:
        return (workspace_paths(self.workspace_path)["outputs"] / "review_queue.csv").exists()

    def _has_built_outputs(self) -> bool:
        return (workspace_paths(self.workspace_path)["outputs"] / "source_chats.csv").exists()

    # ------------------------------------------------------------------ Playwright bootstrap

    def _bootstrap_playwright(self) -> None:
        self.playwright_ok = importlib.util.find_spec("playwright") is not None
        if self.playwright_ok:
            return
        if not messagebox.askyesno(
            "Install browser automation?",
            "Auto-Move needs Playwright + Chromium to drive ChatGPT. Install them now? "
            "(You can also skip and use the manual Move Queue HTML.)",
        ):
            return
        self._install_playwright()

    def _install_playwright(self) -> None:
        if self.running:
            messagebox.showinfo("Busy", "Wait for the current command to finish.")
            return
        self.running = True
        self._set_status("Installing browser automation…")
        self.output_queue.put(("log", "\n$ pip install playwright && playwright install chromium\n"))
        threading.Thread(target=self._install_playwright_thread, daemon=True).start()

    def _install_playwright_thread(self) -> None:
        try:
            steps = [
                [sys.executable, "-m", "pip", "install", "playwright"],
                [sys.executable, "-m", "playwright", "install", "chromium"],
            ]
            last_code = 0
            for cmd in steps:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.output_queue.put(("log", line))
                    s = line.strip()
                    if s:
                        self.output_queue.put(("status", s[:140]))
                last_code = proc.wait()
                if last_code != 0:
                    break
            self.playwright_ok = importlib.util.find_spec("playwright") is not None
            if last_code == 0:
                self.output_queue.put(("status", "Browser automation installed."))
            self.output_queue.put(("done", str(last_code)))
        except Exception as exc:
            self.output_queue.put(("log", f"Error: {exc}\n"))
            self.output_queue.put(("done", "1"))
        finally:
            self.running = False
            # Re-render Move step if user is on it, so the banner updates.
            self.output_queue.put(("callback", lambda _c: self._render_current_step() if self.current_step == 4 else None))


def main() -> None:
    load_tkinter()
    root = tk.Tk()
    ProjectChatsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
