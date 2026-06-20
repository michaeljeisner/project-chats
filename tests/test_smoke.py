import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from project_chats.browser_move import MoveOptions, auto_move, is_logged_in, _profile_dir, session_cookie_header
from project_chats import chatgpt_api
from project_chats.chatgpt_api import FetchOptions, list_conversations, get_access_token, run_fetch
from project_chats.core import normalize_chatgpt_export, persist_chats, load_chats
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

    def test_launcher_helper_runs_or_fails_clearly(self):
        """run-cli.py should either succeed (venv present) or exit 1 with a clear install message."""
        repo = Path(__file__).resolve().parents[1]
        venv_cli = repo / ".venv" / "bin" / "project-chats"
        result = subprocess.run(
            [sys.executable, "scripts/run-cli.py", "--help"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if venv_cli.exists():
            self.assertEqual(result.returncode, 0)
            self.assertIn("project-chats", result.stdout)
        else:
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


class _FakeLocator:
    def __init__(self, n: int) -> None:
        self._n = n

    def count(self) -> int:
        return self._n


class _FakePage:
    """Minimal stand-in for a Playwright page, exercising is_logged_in()."""

    def __init__(self, url="https://chatgpt.com/", composer=False, buttons=(), textbox=False):
        self.url = url
        self._composer = composer
        self._buttons = set(buttons)
        self._textbox = textbox

    def locator(self, selector):
        return _FakeLocator(1 if (selector == "#prompt-textarea" and self._composer) else 0)

    def get_by_role(self, role, name=None):
        if role == "button":
            return _FakeLocator(1 if name in self._buttons else 0)
        if role == "textbox":
            return _FakeLocator(1 if self._textbox else 0)
        return _FakeLocator(0)


class AuthLogicTest(unittest.TestCase):
    def test_logged_in_via_composer(self):
        self.assertTrue(is_logged_in(_FakePage(composer=True)))

    def test_logged_in_via_textbox_only(self):
        self.assertTrue(is_logged_in(_FakePage(textbox=True)))

    def test_logged_out_via_auth_url_overrides_composer(self):
        page = _FakePage(url="https://auth.openai.com/log-in", composer=True)
        self.assertFalse(is_logged_in(page))

    def test_logged_out_via_login_button(self):
        self.assertFalse(is_logged_in(_FakePage(buttons={"Log in"})))

    def test_logged_out_when_empty(self):
        self.assertFalse(is_logged_in(_FakePage()))

    def test_profile_dir_defaults_under_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "run"
            resolved = _profile_dir(workspace, None)
            self.assertEqual(resolved, workspace / "browser-profile")
            self.assertTrue(resolved.is_dir())

    def test_profile_dir_honors_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "custom-profile"
            resolved = _profile_dir(Path(tmp) / "run", override)
            self.assertEqual(resolved, override)
            self.assertTrue(resolved.is_dir())


class LoginCliTest(unittest.TestCase):
    def test_login_command_shape(self):
        command = build_cli_command(
            Path("ws"),
            ["login", "--check", "--user-data-dir", "profiles/alice", "--channel", "chrome"],
        )
        self.assertIn("-m", command)
        self.assertIn("project_chats", command)
        self.assertEqual(command[-4:], ["--user-data-dir", "profiles/alice", "--channel", "chrome"])
        self.assertIn("login", command)

    def test_login_help_runs(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "project_chats", "login", "--help"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--check", result.stdout)


class GuiMoveStepRendersAccountBox(unittest.TestCase):
    def test_move_step_renders_with_account_box(self):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest("Tkinter not available in this Python build.")
        from project_chats import gui as gui_module
        gui_module.load_tkinter()
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            workspace = root_dir / "wsroot" / "atlas"
            # Build a complete workspace so step 4 is reachable.
            init_workspace(workspace, "Atlas", ["Atlas"])
            source = root_dir / "chats.json"
            source.write_text(
                json.dumps(
                    [{
                        "user_label": "alice",
                        "conversation_id": "one",
                        "title": "Atlas plan",
                        "url": "https://chatgpt.com/c/one",
                        "messages": [{"author": "user", "text": "Atlas Atlas Atlas decision."}],
                    }]
                )
            )
            ingest([source], workspace, "alice")
            queue_path = classify(workspace)
            with queue_path.open() as f:
                rows = list(csv.DictReader(f))
            for row in rows:
                row["approved"] = "true"
            write_csv(queue_path, rows, REVIEW_FIELDS)
            build_outputs(workspace)

            original_root = gui_module.default_workspace_root
            gui_module.default_workspace_root = lambda: workspace.parent
            try:
                tk_root = gui_module.tk.Tk()
                tk_root.withdraw()
                app = gui_module.ProjectChatsApp(tk_root)
                app.workspace_path = workspace
                # Widget creation is synchronous; don't pump the event loop
                # (update_idletasks can spin on macOS system Tk for this layout).
                app._goto_step(4)
                # The account-box status var should exist and be wired.
                self.assertIn("sign-in", app.auth_status_var.get().lower())
                tk_root.destroy()
            finally:
                gui_module.default_workspace_root = original_root


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeSession:
    """Routes the exact endpoints chatgpt_api hits, with no network."""

    def __init__(self, convos, details, account_payload=None):
        self.headers = {}
        self._convos = convos
        self._details = details
        self._account_payload = account_payload or {"accounts": {}}

    def get(self, url, headers=None, params=None):
        if url.endswith("/api/auth/session"):
            return _FakeResponse(payload={"accessToken": "tok"})
        if "accounts/check" in url:
            return _FakeResponse(payload=self._account_payload)
        if url.endswith("/conversations"):
            offset = (params or {}).get("offset", 0)
            limit = (params or {}).get("limit", 100)
            page = self._convos[offset : offset + limit]
            return _FakeResponse(payload={"items": page, "total": len(self._convos)})
        if "/conversation/" in url:
            cid = url.rsplit("/", 1)[-1]
            return _FakeResponse(payload=self._details.get(cid, {}))
        return _FakeResponse(status_code=404)


def _detail(title: str, user_text: str, assistant_text: str) -> dict:
    return {
        "title": title,
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["m1"]},
            "m1": {
                "id": "m1",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [user_text]},
                    "create_time": 1700000000,
                },
                "parent": "root",
                "children": ["m2"],
            },
            "m2": {
                "id": "m2",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": [assistant_text]},
                    "create_time": 1700000100,
                },
                "parent": "m1",
                "children": [],
            },
        },
    }


class CookieHeaderTest(unittest.TestCase):
    def test_single_cookie(self):
        cookies = [{"name": "__Secure-next-auth.session-token", "value": "abc"}, {"name": "other", "value": "x"}]
        self.assertEqual(session_cookie_header(cookies), "__Secure-next-auth.session-token=abc")

    def test_split_cookie_order(self):
        cookies = [
            {"name": "__Secure-next-auth.session-token.1", "value": "two"},
            {"name": "__Secure-next-auth.session-token.0", "value": "one"},
        ]
        self.assertEqual(
            session_cookie_header(cookies),
            "__Secure-next-auth.session-token.0=one; __Secure-next-auth.session-token.1=two",
        )

    def test_no_session_cookie(self):
        self.assertEqual(session_cookie_header([{"name": "foo", "value": "bar"}]), "")


class ApiClientTest(unittest.TestCase):
    def test_list_conversations_paginates(self):
        convos = [{"id": f"c{i}", "title": f"T{i}"} for i in range(5)]
        session = _FakeSession(convos, {})
        result = list_conversations(session, "tok", None, limit=None, delay=0, page_size=2)
        self.assertEqual([c["id"] for c in result], ["c0", "c1", "c2", "c3", "c4"])

    def test_list_conversations_respects_limit(self):
        convos = [{"id": f"c{i}", "title": f"T{i}"} for i in range(5)]
        session = _FakeSession(convos, {})
        result = list_conversations(session, "tok", None, limit=3, delay=0, page_size=2)
        self.assertEqual(len(result), 3)

    def test_get_access_token(self):
        session = _FakeSession([], {})
        self.assertEqual(get_access_token(session), "tok")


class FetchEndToEndTest(unittest.TestCase):
    def test_run_fetch_lands_chats_in_workspace(self):
        convos = [{"id": "c0", "title": "Atlas plan"}, {"id": "c1", "title": "Dinner"}]
        details = {
            "c0": _detail("Atlas plan", "Atlas rollout decision", "Decision: staged rollout"),
            "c1": _detail("Dinner", "pasta recipe?", "boil water"),
        }
        session = _FakeSession(convos, details)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "run"
            options = FetchOptions(workspace=workspace, user_label="alice", session_token="fake", delay=0)
            with mock.patch.object(chatgpt_api, "_make_session", return_value=session):
                path = run_fetch(options)
            self.assertTrue(path.exists())
            chats = load_chats(workspace)
            self.assertEqual({c.conversation_id for c in chats}, {"c0", "c1"})
            atlas = next(c for c in chats if c.conversation_id == "c0")
            self.assertIn("staged rollout", "\n".join(m.text for m in atlas.messages))

    def test_run_fetch_respects_limit_and_records_new_ids(self):
        convos = [{"id": f"c{i}", "title": f"T{i}"} for i in range(4)]
        details = {c["id"]: _detail(c["title"], "Atlas note", "ok") for c in convos}
        session = _FakeSession(convos, details)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "run"
            options = FetchOptions(workspace=workspace, user_label="alice", session_token="fake", delay=0, limit=2)
            with mock.patch.object(chatgpt_api, "_make_session", return_value=session):
                run_fetch(options)
            self.assertEqual(load_new_ids(workspace), {"c0", "c1"})


if __name__ == "__main__":
    unittest.main()
