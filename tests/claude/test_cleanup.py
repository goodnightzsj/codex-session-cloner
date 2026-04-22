from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_cli_kit.claude.history_remap import remap_history_identifiers
from ai_cli_kit.claude.models import RunOptions
from ai_cli_kit.claude.paths import default_paths
from ai_cli_kit.claude.services import build_plan, execute_plan, resolve_selection
from ai_cli_kit.claude.tui.app import CleanerTuiApp
from ai_cli_kit.core.tui.screen_mode import (
    ALT_ENTER_FALLBACK,
    ALT_EXIT_FALLBACK,
    ScreenModeDecision,
    TerminfoScreenCaps,
    resolve_screen_mode,
)
from ai_cli_kit.claude.tui.terminal import app_logo_lines, display_width, render_box


class CleanupWorkflowTests(unittest.TestCase):
    def test_safe_plan_marks_session_targets_unselected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.state_file.write_text(json.dumps({"userID": "abc", "keep": 1}), encoding="utf-8")
            paths.telemetry_dir.mkdir()
            (paths.telemetry_dir / "failed.json").write_text("{}", encoding="utf-8")
            paths.projects_dir.mkdir()
            (paths.projects_dir / "session.jsonl").write_text("{}", encoding="utf-8")

            plan = build_plan(paths, resolve_selection("safe"))
            items = {item.target.key: item for item in plan}

            self.assertTrue(items["state_user_id"].selected)
            self.assertTrue(items["telemetry_dir"].selected)
            self.assertFalse(items["projects_dir"].selected)
            self.assertTrue(items["projects_dir"].applicable)

    def test_cleanup_with_backup_scrubs_user_id_and_moves_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.state_file.write_text(json.dumps({"userID": "abc", "keep": 1}), encoding="utf-8")
            paths.telemetry_dir.mkdir()
            (paths.telemetry_dir / "failed.json").write_text("{\"x\":1}", encoding="utf-8")

            selected = {"state_user_id", "telemetry_dir"}
            plan = build_plan(paths, selected)
            summary = execute_plan(paths, plan, RunOptions(backup_enabled=True, dry_run=False))

            payload = json.loads(paths.state_file.read_text(encoding="utf-8"))
            self.assertNotIn("userID", payload)
            self.assertEqual(payload["keep"], 1)
            self.assertFalse(paths.telemetry_dir.exists())
            self.assertIsNotNone(summary.backup_root)

            backup_root = Path(summary.backup_root or "")
            self.assertTrue((backup_root / ".claude.json").exists())
            self.assertTrue((backup_root / ".claude" / "telemetry" / "failed.json").exists())

    def test_scrub_settings_env_removes_only_targeted_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.settings_file.write_text(
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_AUTH_TOKEN": "token",
                            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8317",
                            "KEEP_ME": "1",
                        },
                        "model": "opus",
                    }
                ),
                encoding="utf-8",
            )

            plan = build_plan(paths, {"settings_auth_env"})
            summary = execute_plan(paths, plan, RunOptions(backup_enabled=True, dry_run=False))
            payload = json.loads(paths.settings_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["env"], {"KEEP_ME": "1"})
            self.assertEqual(payload["model"], "opus")
            self.assertEqual(summary.records[0].status, "updated")

    def test_logo_lines_fit_requested_width(self) -> None:
        lines = app_logo_lines(max_width=38)
        self.assertGreaterEqual(len(lines), 2)
        for line in lines:
            self.assertLessEqual(display_width(line), 38)

    def test_render_box_respects_width(self) -> None:
        lines = render_box(["one", "two"], width=32)
        self.assertGreaterEqual(len(lines), 4)
        for line in lines:
            self.assertLessEqual(display_width(line), 32)

    def test_incremental_paint_updates_only_changed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = CleanerTuiApp(default_paths(Path(tmp_dir)))
            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                app._paint_frame("alpha\nbeta\ngamma", force=True)
                buffer.seek(0)
                buffer.truncate(0)
                app._paint_frame("alpha\nBETA\ngamma")

            self.assertEqual(buffer.getvalue(), "\033[2;1H\033[2KBETA")

    def test_home_frame_compacts_on_short_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.state_file.write_text(json.dumps({"userID": "abc"}), encoding="utf-8")
            paths.telemetry_dir.mkdir()
            paths.statsig_dir.mkdir()
            paths.projects_dir.mkdir()
            paths.history_file.write_text("{}", encoding="utf-8")
            paths.sessions_dir.mkdir()

            app = CleanerTuiApp(paths)
            plan = build_plan(paths, resolve_selection("safe"))
            with patch("ai_cli_kit.claude.tui.app.term_height", return_value=24):
                lines = app._frame_lines(app._home_frame(plan))

            self.assertLessEqual(len(lines), 24)
            self.assertTrue(any("更多目标" in line for line in lines))

    def test_screen_mode_env_forces_alt(self) -> None:
        decision = resolve_screen_mode(
            env={"CCC_TUI_SCREEN": "alt", "TERM": "xterm-256color"},
            stdout=io.StringIO(),
            terminfo_caps=TerminfoScreenCaps(False, False),
        )

        self.assertEqual(decision.resolved, "alt")
        self.assertEqual(decision.enter_sequence, ALT_ENTER_FALLBACK + "\033[?25l\033[H")
        self.assertEqual(decision.exit_sequence, ALT_EXIT_FALLBACK + "\033[?25h")

    def test_screen_mode_auto_prefers_main_for_iterm(self) -> None:
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True):
            decision = resolve_screen_mode(
                env={"TERM": "xterm-256color", "TERM_PROGRAM": "iTerm.app"},
                stdout=stream,
                terminfo_caps=TerminfoScreenCaps(True, False, "smcup", "rmcup"),
            )

        self.assertEqual(decision.resolved, "main")
        self.assertIn("terminal profile", decision.reason)

    def test_screen_mode_auto_uses_alt_for_kitty(self) -> None:
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True):
            decision = resolve_screen_mode(
                env={"TERM": "xterm-kitty", "KITTY_WINDOW_ID": "12"},
                stdout=stream,
                terminfo_caps=TerminfoScreenCaps(True, False, "smcup", "rmcup"),
            )

        self.assertEqual(decision.resolved, "alt")
        self.assertEqual(decision.enter_sequence, "smcup\033[?25l\033[H")
        self.assertEqual(decision.exit_sequence, "rmcup\033[?25h")

    def test_screen_mode_auto_respects_tmux_disable(self) -> None:
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True):
            decision = resolve_screen_mode(
                env={"TERM": "tmux-256color", "TMUX": "/tmp/tmux,123,0"},
                stdout=stream,
                terminfo_caps=TerminfoScreenCaps(True, False, "smcup", "rmcup"),
                tmux_alt_screen=False,
            )

        self.assertEqual(decision.resolved, "main")
        self.assertIn("tmux", decision.reason)

    def test_terminal_entry_exit_follow_screen_mode_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = CleanerTuiApp(
                default_paths(Path(tmp_dir)),
                screen_mode=ScreenModeDecision(
                    requested="main",
                    resolved="main",
                    reason="test",
                    enter_sequence="ENTER",
                    exit_sequence="EXIT",
                ),
            )
            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                app._enter_terminal()
                app._leave_terminal()

            self.assertEqual(buffer.getvalue(), "ENTEREXIT")

    def test_remap_history_rewrites_only_structured_identifier_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.projects_dir.mkdir(parents=True)
            paths.sessions_dir.mkdir(parents=True)
            paths.backup_root_base.mkdir(parents=True)
            paths.statsig_dir.mkdir(parents=True)

            old_user_id = "old-user-id-123"
            new_user_id = "new-user-id-456"
            old_stable_id = "old-stable-id-123"
            new_stable_id = "new-stable-id-456"
            old_statsig_session_id = "old-statsig-session-123"
            new_statsig_session_id = "new-statsig-session-456"

            paths.state_file.write_text(json.dumps({"userID": new_user_id}), encoding="utf-8")
            (paths.statsig_dir / "statsig.stable_id.111").write_text(
                json.dumps(new_stable_id),
                encoding="utf-8",
            )
            (paths.statsig_dir / "statsig.cached.evaluations.111").write_text(
                json.dumps(
                    {
                        "stableID": new_stable_id,
                        "data": json.dumps(
                            {
                                "evaluated_keys": {
                                    "userID": new_user_id,
                                    "stableID": new_stable_id,
                                    "customIDs": {"sessionId": new_statsig_session_id},
                                }
                            }
                        ),
                    }
                ),
                encoding="utf-8",
            )

            old_backup_root = paths.backup_root_base / "20260417-010000"
            (old_backup_root / ".claude" / "statsig").mkdir(parents=True)
            (old_backup_root / ".claude.json").write_text(
                json.dumps({"userID": old_user_id}),
                encoding="utf-8",
            )
            (old_backup_root / ".claude" / "statsig" / "statsig.stable_id.999").write_text(
                json.dumps(old_stable_id),
                encoding="utf-8",
            )
            (old_backup_root / ".claude" / "statsig" / "statsig.cached.evaluations.999").write_text(
                json.dumps(
                    {
                        "stableID": old_stable_id,
                        "data": json.dumps(
                            {
                                "evaluated_keys": {
                                    "userID": old_user_id,
                                    "stableID": old_stable_id,
                                    "customIDs": {"sessionId": old_statsig_session_id},
                                }
                            }
                        ),
                    }
                ),
                encoding="utf-8",
            )

            project_file = paths.projects_dir / "session.json"
            project_file.write_text(
                json.dumps(
                    {
                        "userID": old_user_id,
                        "stableID": old_stable_id,
                        "customIDs": {"sessionId": old_statsig_session_id},
                        "note": "keep old-user-id-123 in free text",
                        "sessionId": "conversation-session-should-stay",
                    }
                ),
                encoding="utf-8",
            )
            paths.history_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "userID": old_user_id,
                                "customIDs": {"sessionId": old_statsig_session_id},
                                "sessionId": "history-session-should-stay",
                            }
                        ),
                        "not-json-line",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            session_file = paths.sessions_dir / "74018.json"
            session_file.write_text(
                json.dumps(
                    {
                        "sessionId": "interactive-session-should-stay",
                        "payload": {"stableID": old_stable_id},
                    }
                ),
                encoding="utf-8",
            )

            summary = remap_history_identifiers(
                paths,
                options=RunOptions(backup_enabled=True, dry_run=False),
            )

            project_payload = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(project_payload["userID"], new_user_id)
            self.assertEqual(project_payload["stableID"], new_stable_id)
            self.assertEqual(project_payload["customIDs"]["sessionId"], new_statsig_session_id)
            self.assertEqual(project_payload["note"], "keep old-user-id-123 in free text")
            self.assertEqual(project_payload["sessionId"], "conversation-session-should-stay")

            history_lines = paths.history_file.read_text(encoding="utf-8").splitlines()
            history_payload = json.loads(history_lines[0])
            self.assertEqual(history_payload["userID"], new_user_id)
            self.assertEqual(history_payload["customIDs"]["sessionId"], new_statsig_session_id)
            self.assertEqual(history_payload["sessionId"], "history-session-should-stay")
            self.assertEqual(history_lines[1], "not-json-line")

            session_payload = json.loads(session_file.read_text(encoding="utf-8"))
            self.assertEqual(session_payload["sessionId"], "interactive-session-should-stay")
            self.assertEqual(session_payload["payload"]["stableID"], new_stable_id)

            backup_root = Path(summary.backup_root or "")
            self.assertTrue((backup_root / ".claude" / "projects" / "session.json").exists())
            self.assertTrue((backup_root / ".claude" / "history.jsonl").exists())

    def test_remap_history_can_run_claude_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            paths = default_paths(home)
            paths.claude_dir.mkdir(parents=True)
            paths.statsig_dir.mkdir(parents=True)
            paths.projects_dir.mkdir(parents=True)
            paths.backup_root_base.mkdir(parents=True)
            paths.state_file.write_text(json.dumps({"userID": "new-user"}), encoding="utf-8")
            (paths.statsig_dir / "statsig.stable_id.1").write_text(json.dumps("new-stable"), encoding="utf-8")
            (paths.statsig_dir / "statsig.cached.evaluations.1").write_text(
                json.dumps(
                    {
                        "stableID": "new-stable",
                        "data": json.dumps(
                            {
                                "evaluated_keys": {
                                    "userID": "new-user",
                                    "stableID": "new-stable",
                                    "customIDs": {"sessionId": "new-session"},
                                }
                            }
                        ),
                    }
                ),
                encoding="utf-8",
            )

            old_backup_root = paths.backup_root_base / "20260417-010000"
            (old_backup_root / ".claude" / "statsig").mkdir(parents=True)
            (old_backup_root / ".claude.json").write_text(
                json.dumps({"userID": "old-user"}),
                encoding="utf-8",
            )
            (old_backup_root / ".claude" / "statsig" / "statsig.stable_id.1").write_text(
                json.dumps("old-stable"),
                encoding="utf-8",
            )
            (old_backup_root / ".claude" / "statsig" / "statsig.cached.evaluations.1").write_text(
                json.dumps(
                    {
                        "stableID": "old-stable",
                        "data": json.dumps(
                            {
                                "evaluated_keys": {
                                    "userID": "old-user",
                                    "stableID": "old-stable",
                                    "customIDs": {"sessionId": "old-session"},
                                }
                            }
                        ),
                    }
                ),
                encoding="utf-8",
            )

            with patch("ai_cli_kit.claude.history_remap.subprocess.run") as mocked_run:
                mocked_run.return_value.returncode = 0
                mocked_run.return_value.stdout = "ok"
                mocked_run.return_value.stderr = ""

                summary = remap_history_identifiers(
                    paths,
                    options=RunOptions(backup_enabled=False, dry_run=False),
                    run_claude=True,
                    claude_timeout_seconds=12,
                )

            mocked_run.assert_called_once()
            self.assertEqual(mocked_run.call_args.kwargs["timeout"], 12)
            self.assertTrue(any(record.key == "refresh_claude" for record in summary.records))


if __name__ == "__main__":
    unittest.main()
