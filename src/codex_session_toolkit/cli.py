"""Primary CLI entrypoint for the packaged Codex Session Toolkit."""

from __future__ import annotations

import argparse
import os
import platform
import sys
from typing import Optional, Sequence

from . import APP_COMMAND, APP_DISPLAY_NAME, __version__
from .commands import run_cli as run_toolkit_cli
from .errors import ToolkitError
from .paths import CodexPaths
from .services.provider import detect_provider
from .tui.app import (
    ToolkitAppContext,
    run_cleanup_mode,
    run_clone_mode,
    run_tui,
)
from .tui.terminal import (
    Ansi,
    configure_text_streams as _configure_text_streams,
    horizontal_rule as _hr,
    is_interactive_terminal as _is_interactive,
    style_text as _style,
)

# Configuration
CODEX_ACTIVE_SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
CODEX_CONFIG_PATH = os.path.expanduser("~/.codex/config.toml")
DEFAULT_MODEL_PROVIDER = "cliproxyapi"


def _detect_target_model_provider() -> str:
    try:
        return detect_provider(CodexPaths())
    except ToolkitError:
        return DEFAULT_MODEL_PROVIDER


TARGET_MODEL_PROVIDER = _detect_target_model_provider()
CLI_SUBCOMMANDS = {
    "clone-provider",
    "clean-clones",
    "list",
    "list-bundles",
    "validate-bundles",
    "export",
    "export-desktop-all",
    "export-active-desktop-all",
    "export-cli-all",
    "import",
    "import-desktop-all",
    "repair-desktop",
}


def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description=f"{APP_DISPLAY_NAME}: clone, transfer, import and repair Codex sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Canonical toolkit commands:\n"
            "  clone-provider        Clone active sessions to the current provider\n"
            "  clean-clones          Remove legacy unmarked clone files\n"
            "  list                  Browse local sessions\n"
            "  list-bundles          Browse exported bundle folders\n"
            "  validate-bundles      Validate bundle folder health\n"
            "  export                Export one session bundle\n"
            "  export-desktop-all    Batch export all Desktop sessions\n"
            "  export-active-desktop-all Batch export all active Desktop sessions\n"
            "  export-cli-all        Batch export all CLI sessions\n"
            "  import                Import one bundle\n"
            "  import-desktop-all    Batch import one machine/category folder\n"
            "  repair-desktop        Repair Desktop visibility/index/provider\n\n"
            "Legacy flags still work:\n"
            "  --dry-run             Preview clone mode\n"
            "  --clean               Cleanup legacy clone files\n"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files")
    parser.add_argument("--clean", action="store_true", help="Remove unmarked clones from previous runs")
    parser.add_argument("--no-tui", action="store_true", help="Force CLI mode even in interactive terminal")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def print_header(dry_run: bool) -> None:
    title = _style(f"{APP_DISPLAY_NAME} (Clone Mode)", Ansi.BOLD, Ansi.CYAN)
    print(_hr("="))
    print(title)
    print(_hr("="))
    print(f"OS:            {platform.system()} ({os.name})")
    print(f"Python:        {sys.version.split()[0]}")
    print(f"TargetProvider:{TARGET_MODEL_PROVIDER}")
    print(f"SessionsDir:   {CODEX_ACTIVE_SESSIONS_DIR}")
    print(f"ConfigFile:    {CODEX_CONFIG_PATH}")
    if dry_run:
        print(_style("DRY-RUN MODE (no write / no delete)", Ansi.BOLD, Ansi.YELLOW))
    print(_hr())


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_text_streams()
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] in CLI_SUBCOMMANDS:
        try:
            return run_toolkit_cli(argv)
        except ToolkitError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    parser = create_arg_parser()
    if not argv and _is_interactive():
        try:
            return run_tui(
                ToolkitAppContext(
                    target_provider=TARGET_MODEL_PROVIDER,
                    active_sessions_dir=CODEX_ACTIVE_SESSIONS_DIR,
                    config_path=CODEX_CONFIG_PATH,
                )
            )
        except KeyboardInterrupt:
            return 130

    args = parser.parse_args(argv)
    print_header(dry_run=bool(args.dry_run))

    if args.clean:
        return run_cleanup_mode(
            target_provider=TARGET_MODEL_PROVIDER,
            dry_run=bool(args.dry_run),
            delete_warning="WARNING: --clean will DELETE files.",
        )
    return run_clone_mode(target_provider=TARGET_MODEL_PROVIDER, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
