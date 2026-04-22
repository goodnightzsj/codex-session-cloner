"""Smoke tests for the extracted ``core`` package.

These verify the **structural promise** of PR 1: tool-agnostic primitives are
importable from ``ai_cli_kit.core.*`` AND the legacy import paths
(``ai_cli_kit.codex.support`` / ``ai_cli_kit.codex.tui.terminal``)
still expose the same symbols, so cc-clean integration in PR 2/3 has a stable
contract to land on without breaking existing call sites.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class CoreSupportImportTests(unittest.TestCase):
    def test_core_support_exposes_primitives(self) -> None:
        from ai_cli_kit.core import support as core_support

        for name in (
            "PathEscapeError",
            "atomic_write",
            "ensure_path_within_dir",
            "file_lock",
            "lock_path_for",
            "long_path",
            "nearest_existing_parent",
            "prune_old_backups",
            "replace_with_retry",
            "safe_copy2",
        ):
            self.assertTrue(hasattr(core_support, name), f"core.support is missing {name!r}")

    def test_legacy_support_reexports_match_core(self) -> None:
        """`ai_cli_kit.codex.support` MUST keep exporting the same symbols.

        PR 3 will migrate call sites; until then re-exports are load-bearing.
        """
        from ai_cli_kit.codex import support as legacy
        from ai_cli_kit.core import support as core_support

        for name in (
            "atomic_write",
            "file_lock",
            "lock_path_for",
            "nearest_existing_parent",
            "prune_old_backups",
            "replace_with_retry",
            "safe_copy2",
        ):
            self.assertIs(getattr(legacy, name), getattr(core_support, name))

    def test_legacy_long_path_alias_remains(self) -> None:
        """The underscore-prefixed alias is still imported by some sites."""
        from ai_cli_kit.codex.support import _long_path, long_path

        self.assertIs(_long_path, long_path)


class CoreTuiImportTests(unittest.TestCase):
    def test_core_tui_exposes_primitives(self) -> None:
        from ai_cli_kit.core.tui import terminal as core_terminal

        for name in (
            "Ansi",
            "ANSI_ESCAPE_RE",
            "ASCII_UI_ENV_NAMES",
            "COLOR_ENABLED",
            "clear_screen",
            "configure_text_streams",
            "display_width",
            "ellipsize_middle",
            "env_first",
            "glyphs",
            "is_interactive_terminal",
            "pad_right",
            "read_key",
            "render_box",
            "strip_ansi",
            "style_text",
            "supports_color",
            "term_height",
            "term_width",
        ):
            self.assertTrue(hasattr(core_terminal, name), f"core.tui.terminal is missing {name!r}")

    def test_legacy_terminal_reexports_match_core(self) -> None:
        from ai_cli_kit.core.tui import terminal as core_terminal
        from ai_cli_kit.codex.tui import terminal as legacy

        for name in (
            "Ansi",
            "clear_screen",
            "display_width",
            "ellipsize_middle",
            "glyphs",
            "pad_right",
            "read_key",
            "render_box",
            "style_text",
            "term_height",
            "term_width",
        ):
            self.assertIs(getattr(legacy, name), getattr(core_terminal, name))

    def test_codex_logo_helpers_still_resolve(self) -> None:
        """Codex-specific symbols must remain on ``tui.terminal``."""
        from ai_cli_kit.codex.tui import terminal as legacy

        for name in ("LOGO_FONT_BANNER", "app_logo_lines", "tui_width"):
            self.assertTrue(hasattr(legacy, name), f"tui.terminal is missing codex-specific {name!r}")

    def test_app_logo_lines_returns_strings(self) -> None:
        from ai_cli_kit.codex.tui.terminal import app_logo_lines

        lines = app_logo_lines(80)
        self.assertGreater(len(lines), 0)
        for line in lines:
            self.assertIsInstance(line, str)


class CoreScreenModeTests(unittest.TestCase):
    def test_resolve_returns_main_when_stdout_is_not_tty(self) -> None:
        from ai_cli_kit.core.tui.screen_mode import (
            resolve_screen_mode,
            TerminfoScreenCaps,
        )

        # Provide an env without TTY hints + a non-TTY stub stream.
        class _NotATty:
            def isatty(self) -> bool:
                return False

            def fileno(self) -> int:
                raise OSError("no fileno")

        decision = resolve_screen_mode(
            requested="auto",
            env={"TERM": "xterm-256color"},
            stdout=_NotATty(),
            terminfo_caps=TerminfoScreenCaps(False, False),
            tmux_alt_screen=None,
        )
        self.assertEqual(decision.resolved, "main")
        self.assertIn("TTY", decision.reason)

    def test_explicit_main_override_honored(self) -> None:
        from ai_cli_kit.core.tui.screen_mode import resolve_screen_mode

        decision = resolve_screen_mode(
            requested="main",
            env={"TERM": "xterm-256color"},
        )
        self.assertEqual(decision.resolved, "main")
        self.assertIn("forced", decision.reason)

    def test_normalize_screen_mode_rejects_garbage(self) -> None:
        from ai_cli_kit.core.tui.screen_mode import normalize_screen_mode

        self.assertEqual(normalize_screen_mode("ALT"), "alt")
        self.assertEqual(normalize_screen_mode("  Main  "), "main")
        self.assertEqual(normalize_screen_mode("garbage"), "auto")
        self.assertEqual(normalize_screen_mode(None), "auto")


class CoreLauncherEnvTests(unittest.TestCase):
    def test_launcher_env_defaults_cover_utf8_keys(self) -> None:
        from ai_cli_kit.core.launcher_env import LAUNCHER_ENV_DEFAULTS

        self.assertEqual(LAUNCHER_ENV_DEFAULTS["PYTHONUTF8"], "1")
        self.assertEqual(LAUNCHER_ENV_DEFAULTS["PYTHONIOENCODING"], "utf-8")

    def test_env_was_seeded_detects_present_and_absent(self) -> None:
        from ai_cli_kit.core.launcher_env import env_was_seeded

        self.assertTrue(env_was_seeded({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}))
        self.assertFalse(env_was_seeded({"PYTHONUTF8": "1"}))
        self.assertFalse(env_was_seeded({}))

    def test_launcher_scripts_seed_the_env_vars(self) -> None:
        """Spec/code parity: the launcher scripts must mention each defaulted name."""
        from ai_cli_kit.core.launcher_env import LAUNCHER_ENV_DEFAULTS

        repo_root = Path(__file__).resolve().parents[2]
        # Both the legacy per-tool launchers AND the new top-level ``aik``
        # launchers must seed the same env contract — otherwise a user who
        # invokes ``aik …`` would get different behaviour than ``cst …`` on
        # Windows codepages.
        launcher_paths = [
            repo_root / "codex-session-toolkit",
            repo_root / "codex-session-toolkit.cmd",
            repo_root / "codex-session-toolkit.ps1",
            repo_root / "aik",
            repo_root / "aik.cmd",
            repo_root / "aik.ps1",
        ]
        for launcher in launcher_paths:
            text = launcher.read_text(encoding="utf-8")
            for name in LAUNCHER_ENV_DEFAULTS:
                self.assertIn(
                    name,
                    text,
                    f"{launcher.name} does not mention {name}; launcher and core spec are out of sync",
                )


if __name__ == "__main__":
    unittest.main()
