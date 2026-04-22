import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit import APP_COMMAND, __version__  # noqa: E402
from codex_session_toolkit.cli import create_arg_parser  # noqa: E402
from codex_session_toolkit.tui.app import (  # noqa: E402
    ToolkitAppContext,
    build_tui_menu_actions,
    build_tui_menu_sections,
)
from codex_session_toolkit.tui.terminal import LOGO_FONT_BANNER  # noqa: E402


def _module_env() -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) if not existing else f"{SRC_DIR}{os.pathsep}{existing}"
    return env


class PackagingSmokeTests(unittest.TestCase):
    def test_cli_parser_uses_packaged_command_name(self) -> None:
        parser = create_arg_parser()
        self.assertEqual(parser.prog, APP_COMMAND)

    def test_tui_context_uses_packaged_command_name(self) -> None:
        context = ToolkitAppContext(
            target_provider="demo-provider",
            active_sessions_dir="/tmp/demo-sessions",
            config_path="/tmp/demo-config.toml",
        )
        self.assertEqual(context.entry_command, APP_COMMAND)

    def test_tui_main_sections_are_grouped_by_domain(self) -> None:
        section_ids = [section.section_id for section in build_tui_menu_sections()]
        self.assertEqual(section_ids, ["session", "bundle", "repair"])

        actions_by_section = {}
        for action in build_tui_menu_actions():
            actions_by_section.setdefault(action.section_id, set()).add(action.action_id)

        self.assertEqual(actions_by_section["session"], {"list_sessions", "export_one"})
        self.assertEqual(
            actions_by_section["bundle"],
            {
                "browse_bundles",
                "validate_bundles",
                "export_desktop_all",
                "export_desktop_active",
                "export_cli_all",
                "import_one",
                "import_desktop_all",
            },
        )
        self.assertEqual(
            actions_by_section["repair"],
            {
                "clone",
                "clone_dry",
                "clean",
                "clean_dry",
                "dedupe",
                "dedupe_dry",
                "repair_desktop",
                "repair_desktop_dry",
                "repair_desktop_cli",
                "repair_desktop_cli_dry",
            },
        )

    def test_logo_font_covers_toolkit_wordmark(self) -> None:
        missing = {ch for ch in "CODEX SESSION TOOLKIT" if ch != " " and ch not in LOGO_FONT_BANNER}
        self.assertEqual(missing, set())

    def test_module_help_mentions_packaged_command(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "codex_session_toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn(f"usage: {APP_COMMAND}", result.stdout)
        self.assertIn("clone-provider", result.stdout)

    def test_module_version_matches_package_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "codex_session_toolkit", "--version"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), f"{APP_COMMAND} {__version__}")

    def test_repo_local_launcher_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./codex-session-toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn(f"usage: {APP_COMMAND}", result.stdout)
        self.assertIn("--version", result.stdout)

    def test_repo_local_launcher_prefers_source_mode_in_git_worktree(self) -> None:
        result = subprocess.run(
            ["sh", "./codex-session-toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Launcher (Source Mode)", result.stdout)

    def test_unix_install_script_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./install.sh", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Usage: ./install.sh", result.stdout)
        self.assertIn("--editable", result.stdout)

    def test_release_script_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./release.sh", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Usage: ./release.sh", result.stdout)
        self.assertIn("--output-dir", result.stdout)


if __name__ == "__main__":
    unittest.main()
