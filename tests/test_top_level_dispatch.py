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
    # Force UTF-8 — aik / codex / claude CLIs print Chinese help text, which
    # crashes with UnicodeEncodeError on CI runners whose locale is C/POSIX.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
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

    def test_enter_tool_sets_and_clears_aik_hub_active_env(self) -> None:
        """``_enter_tool`` must mark AIK_HUB_ACTIVE while sub-tool runs.

        Regression guard: this env flag tells codex / claude TUIs to skip
        their own ``\\033[?1049h`` / ``\\033[?1049l`` sequences so the
        hub-to-tool transition doesn't flash the outer shell. Forgetting
        to set it brings the flash back; forgetting to pop it leaks the
        flag into unrelated processes (e.g. when the tool spawns a
        sub-process).
        """
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))

        from ai_cli_kit import cli as cli_mod

        # Spy on _dispatch_to_tool: capture env state, then return cleanly.
        captured = {}

        def fake_dispatch(token, passthrough):
            captured["env_during"] = os.environ.get("AIK_HUB_ACTIVE")
            return 0

        original_dispatch = cli_mod._dispatch_to_tool
        cli_mod._dispatch_to_tool = fake_dispatch
        try:
            self.assertNotIn("AIK_HUB_ACTIVE", os.environ)
            cli_mod._enter_tool("codex")
        finally:
            cli_mod._dispatch_to_tool = original_dispatch

        self.assertEqual(captured.get("env_during"), "1", "AIK_HUB_ACTIVE not set during sub-tool")
        self.assertNotIn("AIK_HUB_ACTIVE", os.environ, "AIK_HUB_ACTIVE leaked after sub-tool exit")

    def test_hub_cards_are_horizontally_centred(self) -> None:
        """Cards must have non-trivial left padding on a wide terminal.

        Regression guard: an earlier hub used a hard-coded ``"  "`` 2-space
        indent regardless of terminal width, leaving the cards visually
        skewed left of the centred banner above them. The fix computes
        ``card_pad = (cols - card_width - marker_width) // 2`` so the cards
        line up with the banner.
        """
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))
        import io
        import re

        from ai_cli_kit.cli import _render_hub
        from ai_cli_kit.core.tui import terminal as core_terminal

        # Force a wide terminal so the centred padding is unambiguous.
        original_term_width = core_terminal.term_width
        core_terminal.term_width = lambda fallback=90: 120
        # Patch the cli's reference too — it imported the symbol directly.
        from ai_cli_kit import cli as cli_mod
        original_cli_term_width = cli_mod.term_width
        cli_mod.term_width = lambda fallback=90: 120

        buf = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            _render_hub(0)
        finally:
            sys.stdout = original_stdout
            core_terminal.term_width = original_term_width
            cli_mod.term_width = original_cli_term_width

        plain_lines = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue()).splitlines()
        card_top_lines = [ln for ln in plain_lines if "┌" in ln]
        self.assertGreaterEqual(len(card_top_lines), 1)
        # The leading whitespace of the card top border must be > 8 cols on a
        # 120-col terminal — anything less means we regressed to the old
        # "hard-coded 2-space indent" left-alignment.
        for top in card_top_lines:
            indent = len(top) - len(top.lstrip(" "))
            self.assertGreater(indent, 8, f"card row {top!r} not centred (indent={indent})")


if __name__ == "__main__":
    unittest.main()


class CodexSubflowCenteringTests(unittest.TestCase):
    """Regression guard: every codex/tui/app.py render_box render path must
    go through ``_print_centered_box`` so sub-flows (browser, action panel,
    detail view) stay centred. The legacy ``for line in render_box(...):
    print(line)`` pattern bypasses centring and reads as "stuck left"
    against the centred header above.
    """

    def test_no_bare_print_line_after_render_box(self) -> None:
        path = ROOT_DIR / "src" / "ai_cli_kit" / "codex" / "tui" / "app.py"
        text = path.read_text(encoding="utf-8")
        # The single legitimate ``print(line)`` left in the file iterates
        # ``_brand_header_lines`` which already applied align_line internally.
        # Any new ``for line in render_box(...):`` followed by ``print(line)``
        # would be a regression.
        import re

        bad = re.findall(
            r"for[ \t]+line[ \t]+in[ \t]+render_box\([^)]*\):\s*\n[ \t]+print\(line\)",
            text,
        )
        self.assertEqual(
            bad, [],
            f"Found {len(bad)} bare 'for line in render_box(...): print(line)' "
            "pattern(s) — use self._print_centered_box(render_box(...)) instead."
        )
