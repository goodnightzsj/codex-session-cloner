"""Top-level ``aik`` dispatcher smoke tests.

These exercise the routing contract that ties the unified ``aik`` entry
point to the per-tool ``main(argv)`` functions. They run as subprocesses
because the dispatcher imports the tool packages on demand and we want to
catch any import-time regression that doesn't surface in unit tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"


def _module_env() -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) if not existing else f"{SRC_DIR}{os.pathsep}{existing}"
    return env


class AikDispatchTests(unittest.TestCase):
    def test_version_short_circuits_before_dispatch(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "ai_cli_kit", "--version"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("aik ", result.stdout)

    def test_help_lists_known_tools(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "ai_cli_kit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("codex", result.stdout)
        self.assertIn("claude", result.stdout)
        self.assertIn("aik <tool>", result.stdout)

    def test_unknown_tool_exits_nonzero_with_hint(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "ai_cli_kit", "definitely-not-a-tool"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown tool", result.stdout)

    def test_dispatch_to_codex_help_uses_codex_parser(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "ai_cli_kit", "codex", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("usage: codex-session-toolkit", result.stdout)
        self.assertIn("clone-provider", result.stdout)

    def test_dispatch_to_claude_help_uses_claude_parser(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "ai_cli_kit", "claude", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("usage: cc-clean", result.stdout)
        self.assertIn("plan", result.stdout)

    def test_aik_shell_launcher_runs_help(self) -> None:
        result = subprocess.run(
            ["sh", "./aik", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("AI CLI Kit", result.stdout)
        self.assertIn("Tools:", result.stdout)

    def test_cc_clean_launcher_forwards_to_claude(self) -> None:
        result = subprocess.run(
            ["sh", "./cc-clean", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("usage: cc-clean", result.stdout)


class HubLogoTests(unittest.TestCase):
    """Hub renders the shared pixel-art wordmark, not plain text only."""

    def test_aik_logo_lines_render_with_pixel_glyphs(self) -> None:
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))
        from ai_cli_kit.cli import _aik_logo_lines

        lines = _aik_logo_lines(80)
        self.assertGreaterEqual(len(lines), 4, "expected multi-row pixel-art logo")
        # On terminals supporting Unicode, the logo MUST contain block-fill
        # glyphs (or the ASCII fallback "#") — otherwise we lost the pixel-art
        # banner and regressed to the previous text-only header.
        joined = "".join(lines)
        self.assertTrue(
            "█" in joined or "#" in joined,
            f"expected pixel fill character in logo, got {joined[:80]!r}",
        )

    def test_render_hub_emits_cards_and_esc_hint(self) -> None:
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))
        import io
        import re

        from ai_cli_kit.cli import _render_hub

        buf = io.StringIO()
        original = sys.stdout
        sys.stdout = buf
        try:
            _render_hub(0)
        finally:
            sys.stdout = original
        plain = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("Codex Session Toolkit", plain)
        self.assertIn("CC Clean", plain)
        # Footer must surface Esc as an exit option (alongside q) so users
        # don't have to guess.
        self.assertIn("Esc", plain)


if __name__ == "__main__":
    unittest.main()
