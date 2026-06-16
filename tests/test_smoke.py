import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from project_chats.browser_move import MoveOptions, auto_move
from project_chats.core import (
    ProjectChatsError,
    REVIEW_FIELDS,
    build_outputs,
    bundle,
    classify,
    default_workspace_root,
    ingest,
    init_workspace,
    load_new_ids,
    slugify_project_name,
    write_csv,
)
from project_chats.gui import build_cli_command


class SmokeTest(unittest.TestCase):
    def test_full_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "run"
            source = root / "chats.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "user_label": "alice",
                            "conversation_id": "one",
                            "title": "Atlas fidelity plan",
                            "url": "https://chatgpt.com/c/one",
                            "messages": [
                                {
                                    "author": "user",
                                    "text": "Atlas needs a final decision and fidelity review.",
                                }
                            ],
                        },
                        {
                            "user_label": "alice",
                            "conversation_id": "two",
                            "title": "Dinner",
                            "messages": [{"author": "user", "text": "Pasta recipe"}],
                        },
                    ]
                )
            )

            init_workspace(workspace, "Project Atlas", ["Atlas", "fidelity"])
            ingest([source], workspace, "alice")
            queue = classify(workspace)
            with queue.open() as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["conversation_id"], "one")

            outputs = build_outputs(workspace)
            self.assertTrue((workspace / "outputs" / "move_queue.html").exists())
            self.assertGreaterEqual(len(outputs), 6)

            archive = bundle(workspace)
            self.assertTrue(archive.exists())

            move_log = auto_move(MoveOptions(workspace=workspace, dry_run=True))
            self.assertTrue(move_log.exists())
            with move_log.open() as f:
                move_rows = list(csv.DictReader(f))
            self.assertEqual(move_rows[0]["status"], "dry_run")

    def test_gui_command_uses_cli_under_the_hood(self):
        command = build_cli_command(Path("custom-workspace"), ["classify"])
        self.assertIn("-m", command)
        self.assertIn("project_chats", command)
        self.assertIn("--workspace", command)
        self.assertIn("custom-workspace", command)
        self.assertEqual(command[-1], "classify")

    def test_gui_auto_move_options_map_to_cli(self):
        command = build_cli_command(
            Path("custom-workspace"),
            [
                "auto-move",
                "--user-label",
                "alice",
                "--project-name",
                "Project Atlas",
                "--user-data-dir",
                "profiles/alice",
                "--channel",
                "chrome",
                "--limit",
                "1",
            ],
        )
        self.assertIn("--user-data-dir", command)
        self.assertIn("profiles/alice", command)
        self.assertIn("--channel", command)
        self.assertIn("chrome", command)
        self.assertIn("--project-name", command)

    def test_launcher_helpers_fail_clearly_before_install(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/run-cli.py", "--help"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("scripts/install.py", result.stderr)


class ErrorAndWorkspaceTest(unittest.TestCase):
    def test_classify_missing_chats_raises_project_chats_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "run"
            init_workspace(workspace, "Atlas", ["Atlas"])
            with self.assertRaises(ProjectChatsError):
                classify(workspace)

    def test_cli_exits_nonzero_on_project_chats_error_with_message(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "run"
            init_workspace(workspace, "Atlas", ["Atlas"])
            result = subprocess.run(
                [sys.executable, "-m", "project_chats", "--workspace", str(workspace), "classify"],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("No ingested chats found", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_default_workspace_root_per_platform(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIn("Application Support", str(default_workspace_root()))
            self.assertIn("Project Chats", str(default_workspace_root()))
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}, clear=False):
            self.assertIn("Project Chats", str(default_workspace_root()))
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.dict("os.environ", {"XDG_DATA_HOME": "/tmp/share"}, clear=False):
            self.assertEqual(default_workspace_root(), Path("/tmp/share/project-chats"))

    def test_slugify_project_name(self):
        self.assertEqual(slugify_project_name("Project Atlas!"), "project-atlas")
        self.assertEqual(slugify_project_name("   "), "untitled")
        self.assertEqual(slugify_project_name("ALPHA.beta_v2"), "alpha.beta_v2")


class IncrementalIngestTest(unittest.TestCase):
    def _write_chats(self, path: Path, chats: list[dict]) -> None:
        path.write_text(json.dumps(chats))

    def _make_chat(self, cid: str, title: str) -> dict:
        return {
            "user_label": "alice",
            "conversation_id": cid,
            "title": title,
            "url": f"https://chatgpt.com/c/{cid}",
            "messages": [{"author": "user", "text": f"Atlas note for {cid}"}],
        }

    def test_first_ingest_marks_all_chats_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "run"
            init_workspace(workspace, "Atlas", ["Atlas"])
            source = root / "chats.json"
            self._write_chats(source, [self._make_chat("a", "A"), self._make_chat("b", "B")])
            ingest([source], workspace, "alice")
            self.assertEqual(load_new_ids(workspace), {"a", "b"})

    def test_second_ingest_marks_only_delta_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "run"
            init_workspace(workspace, "Atlas", ["Atlas"])
            first = root / "first.json"
            self._write_chats(first, [self._make_chat("a", "A"), self._make_chat("b", "B")])
            ingest([first], workspace, "alice")

            second = root / "second.json"
            self._write_chats(second, [self._make_chat("b", "B"), self._make_chat("c", "C")])
            ingest([second], workspace, "alice")

            self.assertEqual(load_new_ids(workspace), {"c"})


class GuiHeadlessTest(unittest.TestCase):
    def test_instantiation_and_step_navigation(self):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest("Tkinter not available in this Python build.")
        from project_chats import gui as gui_module
        gui_module.load_tkinter()
        with tempfile.TemporaryDirectory() as td:
            original_root = gui_module.default_workspace_root
            gui_module.default_workspace_root = lambda: Path(td)
            try:
                root = gui_module.tk.Tk()
                root.withdraw()
                app = gui_module.ProjectChatsApp(root)
                for step in (1, 2, 3, 4, 1):
                    app._goto_step(step)
                    root.update_idletasks()
                root.destroy()
            finally:
                gui_module.default_workspace_root = original_root


class ReviewCsvRoundTripTest(unittest.TestCase):
    def test_gui_save_path_round_trips_through_build_outputs(self):
        """Simulate what the GUI ReviewPane does: read review_queue.csv, flip an approved column, write_csv back, then run build_outputs and confirm the new approval set is reflected."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "run"
            init_workspace(workspace, "Atlas", ["Atlas", "fidelity"])
            source = root / "chats.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "user_label": "alice",
                            "conversation_id": "one",
                            "title": "Atlas fidelity plan",
                            "url": "https://chatgpt.com/c/one",
                            "messages": [{"author": "user", "text": "Atlas fidelity decision review."}],
                        },
                        {
                            "user_label": "alice",
                            "conversation_id": "two",
                            "title": "Atlas standup",
                            "url": "https://chatgpt.com/c/two",
                            "messages": [{"author": "user", "text": "Atlas standup notes; Atlas, Atlas, Atlas, fidelity."}],
                        },
                    ]
                )
            )
            ingest([source], workspace, "alice")
            queue_path = classify(workspace)

            with queue_path.open() as f:
                rows = list(csv.DictReader(f))
            self.assertGreaterEqual(len(rows), 1)

            # GUI behavior: flip every row to approved=true, then write back.
            for row in rows:
                row["approved"] = "true"
            write_csv(queue_path, rows, REVIEW_FIELDS)

            outputs = build_outputs(workspace)
            source_chats = next(p for p in outputs if p.name == "source_chats.csv")
            with source_chats.open() as f:
                source_rows = list(csv.DictReader(f))
            self.assertEqual(len(source_rows), len(rows))


if __name__ == "__main__":
    unittest.main()
