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


if __name__ == "__main__":
    unittest.main()
