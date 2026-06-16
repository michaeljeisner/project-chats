import csv
import json
import tempfile
import unittest
from pathlib import Path

from project_chats.browser_move import MoveOptions, auto_move
from project_chats.core import build_outputs, bundle, classify, ingest, init_workspace
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


if __name__ == "__main__":
    unittest.main()
