from __future__ import annotations

import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from .core import DEFAULT_WORKSPACE


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
    global BOTH, END, LEFT, RIGHT, X, Button, Checkbutton, Entry, Frame
    global Label, LabelFrame, Notebook, StringVar, Text, Tk, filedialog, messagebox

    try:
        from tkinter import (
            BOTH,
            END,
            LEFT,
            RIGHT,
            X,
            Button,
            Checkbutton,
            Entry,
            Frame,
            Label,
            LabelFrame,
            StringVar,
            Text,
            Tk,
            filedialog,
            messagebox,
        )
        from tkinter.ttk import Notebook
    except ImportError as exc:
        raise SystemExit(
            "The desktop GUI requires Python Tkinter. Install a Python build with Tk support, "
            "or use the CLI with `project-chats --help`."
        ) from exc


class ProjectChatsApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Project Chats")
        self.root.geometry("960x720")
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.running = False

        self.workspace_var = StringVar(value=str(DEFAULT_WORKSPACE))
        self.project_var = StringVar(value="")
        self.terms_var = StringVar(value="")
        self.user_label_var = StringVar(value="")
        self.input_path_var = StringVar(value="")
        self.limit_var = StringVar(value="")
        self.dry_run_var = StringVar(value="1")

        self.build_layout()
        self.root.after(100, self.drain_output_queue)

    def build_layout(self) -> None:
        top = Frame(self.root, padx=12, pady=10)
        top.pack(fill=X)

        Label(top, text="Workspace").pack(side=LEFT)
        Entry(top, textvariable=self.workspace_var).pack(side=LEFT, fill=X, expand=True, padx=8)
        Button(top, text="Choose", command=self.choose_workspace).pack(side=LEFT)

        tabs = Notebook(self.root)
        tabs.pack(fill=BOTH, expand=True, padx=12, pady=(0, 8))

        setup_tab = Frame(tabs, padx=12, pady=12)
        run_tab = Frame(tabs, padx=12, pady=12)
        outputs_tab = Frame(tabs, padx=12, pady=12)
        tabs.add(setup_tab, text="Setup")
        tabs.add(run_tab, text="Run")
        tabs.add(outputs_tab, text="Outputs")

        self.build_setup_tab(setup_tab)
        self.build_run_tab(run_tab)
        self.build_outputs_tab(outputs_tab)

        log_frame = LabelFrame(self.root, text="Command Log", padx=8, pady=8)
        log_frame.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))
        self.log = Text(log_frame, height=12, wrap="word")
        self.log.pack(side=LEFT, fill=BOTH, expand=True)
        Button(log_frame, text="Clear", command=lambda: self.log.delete("1.0", END)).pack(side=RIGHT, anchor="n")

    def build_setup_tab(self, parent: Frame) -> None:
        Label(parent, text="Project name").pack(anchor="w")
        Entry(parent, textvariable=self.project_var).pack(fill=X, pady=(2, 10))

        Label(parent, text="Terms, separated by commas").pack(anchor="w")
        Entry(parent, textvariable=self.terms_var).pack(fill=X, pady=(2, 10))

        row = Frame(parent)
        row.pack(fill=X, pady=8)
        Button(row, text="Create / Replace Profile", command=self.create_profile).pack(side=LEFT)
        Button(row, text="Open Profile JSON", command=lambda: self.open_path("project_profile.json")).pack(side=LEFT, padx=8)

        help_text = (
            "Use this once per project. Terms can be project names, aliases, people, repos, clients, "
            "domains, or other words that identify relevant chats."
        )
        Label(parent, text=help_text, wraplength=760, justify=LEFT).pack(anchor="w", pady=12)

    def build_run_tab(self, parent: Frame) -> None:
        ingest_frame = LabelFrame(parent, text="Ingest", padx=10, pady=10)
        ingest_frame.pack(fill=X, pady=(0, 12))

        Label(ingest_frame, text="User label").pack(anchor="w")
        Entry(ingest_frame, textvariable=self.user_label_var).pack(fill=X, pady=(2, 8))

        Label(ingest_frame, text="Input file or folder").pack(anchor="w")
        input_row = Frame(ingest_frame)
        input_row.pack(fill=X, pady=(2, 8))
        Entry(input_row, textvariable=self.input_path_var).pack(side=LEFT, fill=X, expand=True)
        Button(input_row, text="File", command=self.choose_input_file).pack(side=LEFT, padx=(8, 0))
        Button(input_row, text="Folder", command=self.choose_input_folder).pack(side=LEFT, padx=(8, 0))
        Button(ingest_frame, text="Ingest", command=self.ingest).pack(anchor="w")

        workflow = LabelFrame(parent, text="Workflow", padx=10, pady=10)
        workflow.pack(fill=X, pady=(0, 12))
        Button(workflow, text="Classify", command=lambda: self.run_cli(["classify"])).pack(side=LEFT)
        Button(workflow, text="Build Outputs", command=lambda: self.run_cli(["build"])).pack(side=LEFT, padx=8)
        Button(workflow, text="Bundle Zip", command=lambda: self.run_cli(["bundle"])).pack(side=LEFT)

        move = LabelFrame(parent, text="Auto-Move Approved Chats", padx=10, pady=10)
        move.pack(fill=X)
        Checkbutton(move, text="Dry run", variable=self.dry_run_var, onvalue="1", offvalue="0").pack(side=LEFT)
        Label(move, text="Limit").pack(side=LEFT, padx=(14, 4))
        Entry(move, textvariable=self.limit_var, width=8).pack(side=LEFT)
        Button(move, text="Auto-Move", command=self.auto_move).pack(side=LEFT, padx=10)

    def build_outputs_tab(self, parent: Frame) -> None:
        outputs = [
            ("Review Queue", "outputs/review_queue.html"),
            ("Move Queue", "outputs/move_queue.html"),
            ("Project Brief", "outputs/project_brief.md"),
            ("Decisions", "outputs/decisions.md"),
            ("Requirements", "outputs/requirements.md"),
            ("Open Questions", "outputs/open_questions.md"),
            ("Source Chats CSV", "outputs/source_chats.csv"),
            ("Move Log CSV", "outputs/move_log.csv"),
            ("Handoff Zip", "project-chat-handoff.zip"),
        ]
        for label, rel_path in outputs:
            row = Frame(parent)
            row.pack(fill=X, pady=3)
            Label(row, text=label, width=20, anchor="w").pack(side=LEFT)
            Button(row, text="Open", command=lambda path=rel_path: self.open_path(path)).pack(side=LEFT)

        Button(parent, text="Open Workspace Folder", command=self.open_workspace).pack(anchor="w", pady=14)

    def workspace(self) -> Path:
        return Path(self.workspace_var.get()).expanduser()

    def choose_workspace(self) -> None:
        selected = filedialog.askdirectory(title="Choose workspace")
        if selected:
            self.workspace_var.set(selected)

    def choose_input_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose chat input",
            filetypes=[("Supported files", "*.json *.md *.txt"), ("All files", "*.*")],
        )
        if selected:
            self.input_path_var.set(selected)

    def choose_input_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose input folder")
        if selected:
            self.input_path_var.set(selected)

    def create_profile(self) -> None:
        project_name = self.project_var.get().strip()
        terms = [term.strip() for term in self.terms_var.get().split(",") if term.strip()]
        if not project_name:
            messagebox.showerror("Missing project name", "Enter a project name.")
            return
        if not terms:
            messagebox.showerror("Missing terms", "Enter at least one search term.")
            return
        args = ["init", "--project-name", project_name, "--force"]
        for term in terms:
            args.extend(["--term", term])
        self.run_cli(args)

    def ingest(self) -> None:
        user_label = self.user_label_var.get().strip()
        input_path = self.input_path_var.get().strip()
        if not user_label:
            messagebox.showerror("Missing user label", "Enter a user label.")
            return
        if not input_path:
            messagebox.showerror("Missing input", "Choose an input file or folder.")
            return
        self.run_cli(["ingest", input_path, "--user-label", user_label])

    def auto_move(self) -> None:
        args = ["auto-move"]
        if self.user_label_var.get().strip():
            args.extend(["--user-label", self.user_label_var.get().strip()])
        if self.dry_run_var.get() == "1":
            args.append("--dry-run")
        if self.limit_var.get().strip():
            args.extend(["--limit", self.limit_var.get().strip()])
        self.run_cli(args)

    def run_cli(self, args: list[str]) -> None:
        if self.running:
            messagebox.showinfo("Command running", "Wait for the current command to finish.")
            return
        command = build_cli_command(self.workspace(), args)
        self.running = True
        self.output_queue.put(f"\n$ {' '.join(command)}\n")
        thread = threading.Thread(target=self.run_command_thread, args=(command,), daemon=True)
        thread.start()

    def run_command_thread(self, command: list[str]) -> None:
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
                self.output_queue.put(line)
            code = process.wait()
            self.output_queue.put(f"Command exited with code {code}\n")
        except Exception as exc:  # noqa: BLE001 - surface GUI command errors to the user.
            self.output_queue.put(f"Error: {exc}\n")
        finally:
            self.running = False

    def drain_output_queue(self) -> None:
        while True:
            try:
                line = self.output_queue.get_nowait()
            except queue.Empty:
                break
            self.log.insert(END, line)
            self.log.see(END)
        self.root.after(100, self.drain_output_queue)

    def open_path(self, rel_path: str) -> None:
        path = self.workspace() / rel_path
        if not path.exists():
            messagebox.showerror("Missing file", f"{path} does not exist yet.")
            return
        webbrowser.open(path.resolve().as_uri())

    def open_workspace(self) -> None:
        path = self.workspace()
        path.mkdir(parents=True, exist_ok=True)
        webbrowser.open(path.resolve().as_uri())


def main() -> None:
    load_tkinter()
    root = Tk()
    ProjectChatsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
