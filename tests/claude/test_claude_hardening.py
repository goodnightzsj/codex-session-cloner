"""Claude subpackage cross-platform hardening tests.

Locks in the invariants we just established so a future regression that
removes ``atomic_write`` / ``shutil.which`` / ``newline=""`` / ``safe_copy2``
fails fast in CI rather than corrupting state on a user's Windows install.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from ai_cli_kit.claude.history_remap import _run_claude_refresh, _rewrite_file_in_place  # noqa: E402
from ai_cli_kit.claude.paths import default_paths  # noqa: E402
from ai_cli_kit.claude.services import (  # noqa: E402
    _move_with_retry,
    _relative_under_home,
    _remove_with_retry,
    _write_json,
    build_plan,
    execute_plan,
    resolve_selection,
)
from ai_cli_kit.claude.models import RunOptions  # noqa: E402


class AtomicJsonWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_dir = Path(self.tmp.name)

    def test_write_json_replaces_target_atomically(self) -> None:
        target = self.tmp_dir / "state.json"
        target.write_text(json.dumps({"old": True}), encoding="utf-8")
        _write_json(target, {"new": True})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": True})

    def test_write_json_leaves_no_partial_tempfile_after_success(self) -> None:
        target = self.tmp_dir / "settings.json"
        _write_json(target, {"x": 1})
        leftovers = sorted(p.name for p in self.tmp_dir.iterdir() if p.suffix == ".tmp")
        self.assertEqual(leftovers, [])


class ClaudeBinaryDiscoveryTests(unittest.TestCase):
    def test_run_claude_refresh_errors_when_executable_missing(self) -> None:
        # When ``claude`` is not on PATH, we surface a clear error instead of
        # crashing with ``FileNotFoundError`` mid-subprocess.
        with patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                _run_claude_refresh(timeout_seconds=1)
        self.assertIn("PATH", str(ctx.exception))

    def test_run_claude_refresh_uses_resolved_executable(self) -> None:
        # Mock both shutil.which (used by our code) and subprocess.run to
        # capture how the resolved executable is forwarded.
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

            class _Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return _Result()

        with patch("shutil.which", return_value="/fake/path/to/claude"):
            with patch("ai_cli_kit.claude.history_remap.subprocess.run", side_effect=fake_run):
                _run_claude_refresh(timeout_seconds=7)

        self.assertEqual(captured["cmd"][0], "/fake/path/to/claude")
        self.assertEqual(captured["kwargs"]["timeout"], 7)


class HistoryRemapWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_dir = Path(self.tmp.name)

    def test_rewrite_jsonl_keeps_lf_only_line_endings(self) -> None:
        # JSONL files must not get CRLF translation on Windows; ``newline=""``
        # in atomic_write is what guarantees that.
        target = self.tmp_dir / "history.jsonl"
        target.write_text(
            json.dumps({"userID": "old-id"}) + "\n" + json.dumps({"x": 1}) + "\n",
            encoding="utf-8",
        )
        mappings = {"user_id": ("old-id", "new-id")}
        _rewrite_file_in_place(target, mappings)

        # Verify the rewrite happened AND line endings are LF-only.
        body = target.read_bytes()
        self.assertNotIn(b"\r\n", body)
        first_line = body.split(b"\n", 1)[0]
        self.assertEqual(json.loads(first_line)["userID"], "new-id")


class RetryableFileOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_dir = Path(self.tmp.name)

    def test_move_with_retry_succeeds_on_first_try(self) -> None:
        src = self.tmp_dir / "src.txt"
        src.write_text("payload", encoding="utf-8")
        dst = self.tmp_dir / "subdir" / "dst.txt"
        dst.parent.mkdir(parents=True)
        _move_with_retry(src, dst)
        self.assertEqual(dst.read_text(encoding="utf-8"), "payload")
        self.assertFalse(src.exists())

    def test_remove_with_retry_handles_files_and_dirs(self) -> None:
        a_file = self.tmp_dir / "f.txt"
        a_file.write_text("x", encoding="utf-8")
        a_dir = self.tmp_dir / "d"
        (a_dir / "child").mkdir(parents=True)
        (a_dir / "child" / "nested.txt").write_text("y", encoding="utf-8")

        _remove_with_retry(a_file)
        _remove_with_retry(a_dir)
        self.assertFalse(a_file.exists())
        self.assertFalse(a_dir.exists())


class RelativeUnderHomeCaseTests(unittest.TestCase):
    def test_path_inside_home_returns_relative_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            (home / ".claude").mkdir()
            target = home / ".claude" / "state.json"
            self.assertEqual(_relative_under_home(home, target), Path(".claude/state.json"))

    def test_path_outside_home_falls_through_to_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir) / "home"
            home.mkdir()
            outside = Path(tmp_dir) / "elsewhere" / "x"
            self.assertTrue(str(_relative_under_home(home, outside)).startswith("external"))

    def test_case_variant_path_treated_as_inside_on_insensitive_fs(self) -> None:
        if os.path.normcase("ABC") == "ABC":
            self.skipTest("case-sensitive filesystem; normcase is identity")
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir) / "home"
            (home / ".claude").mkdir(parents=True)
            up_target = Path(str(home).upper()) / ".claude" / "state.json"
            relative = _relative_under_home(home, up_target)
            self.assertFalse(str(relative).startswith("external"))


class FullCleanupRoundtripTests(unittest.TestCase):
    """End-to-end: post-hardening, the safe preset still works as before."""

    def test_safe_preset_atomic_write_cleans_userid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.state_file.write_text(
                json.dumps({"userID": "abc", "keep": 1}), encoding="utf-8"
            )
            paths.telemetry_dir.mkdir()
            (paths.telemetry_dir / "fail.json").write_text("{}", encoding="utf-8")

            plan = build_plan(paths, resolve_selection("safe"))
            summary = execute_plan(paths, plan, RunOptions(backup_enabled=True, dry_run=False))

            payload = json.loads(paths.state_file.read_text(encoding="utf-8"))
            self.assertNotIn("userID", payload)
            self.assertEqual(payload["keep"], 1)
            self.assertFalse(paths.telemetry_dir.exists())
            self.assertIsNotNone(summary.backup_root)


if __name__ == "__main__":
    unittest.main()
