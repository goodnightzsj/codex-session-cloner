"""Top-level ``aik`` dispatcher and TUI hub.

Routes ``aik <tool> <subcommand>`` into the matching subpackage CLI:

* ``aik codex …`` → :func:`ai_cli_kit.codex.cli.main`
* ``aik claude …`` → :func:`ai_cli_kit.claude.cli.main`

When invoked with no arguments on an interactive TTY, opens an interactive hub
that lets the user pick a tool, then transfers control to that tool's TUI.

Backwards-compatible entry points (``codex-session-toolkit``, ``cst``,
``cc-clean``) bypass this dispatcher and call the per-tool ``main`` directly,
so existing scripts and shell aliases continue to work unchanged.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Sequence

from . import APP_COMMAND, APP_DISPLAY_NAME, __version__
from .core.tui.terminal import (
    Ansi,
    clear_screen,
    configure_text_streams,
    glyphs,
    is_interactive_terminal,
    read_key,
    render_box,
    style_text,
)


# Tool registry — pairs the public token (``codex`` / ``claude``) with the
# entry point function and a short label for the hub. Adding a new sibling
# tool means appending one tuple here plus its launcher script.
_TOOLS = (
    ("codex", "Codex CLI Session Toolkit", "ai_cli_kit.codex.cli", "克隆 / 导出 / 导入 / 修复 Codex 会话"),
    ("claude", "Claude Code Local Cleanup", "ai_cli_kit.claude.cli", "清理本地标识 / 遥测 / 历史，安全备份"),
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_text_streams()
    argv = list(sys.argv[1:] if argv is None else argv)

    # Help / version short-circuits — must precede the dispatch table so the
    # hub never gets entered when the user only wants info.
    if argv and argv[0] in {"-h", "--help", "help"}:
        _print_top_help()
        return 0
    if argv and argv[0] in {"-V", "--version"}:
        sys.stdout.write(f"{APP_COMMAND} {__version__}\n")
        return 0

    # Sub-tool dispatch.
    if argv and argv[0] in {token for token, _, _, _ in _TOOLS}:
        tool_token = argv[0]
        passthrough = argv[1:]
        return _dispatch_to_tool(tool_token, passthrough)

    # Unknown subcommand → show help with a hint.
    if argv and not argv[0].startswith("-"):
        sys.stderr.write(
            f"{APP_COMMAND}: unknown tool '{argv[0]}'. "
            f"Known tools: {', '.join(token for token, _, _, _ in _TOOLS)}\n"
        )
        _print_top_help(stream=sys.stderr)
        return 2

    # No args + interactive TTY → open the hub.
    if not argv and is_interactive_terminal():
        return _run_hub()

    # No args, non-interactive (piped / scripted) → just show help.
    _print_top_help()
    return 0


def _dispatch_to_tool(tool_token: str, passthrough: Sequence[str]) -> int:
    """Import the tool's CLI module on demand and call its ``main(argv)``."""
    import importlib

    module_name = next(module for token, _, module, _ in _TOOLS if token == tool_token)
    module = importlib.import_module(module_name)
    tool_main = getattr(module, "main", None)
    if not callable(tool_main):
        sys.stderr.write(f"{APP_COMMAND}: {module_name}.main is not callable\n")
        return 1
    return int(tool_main(list(passthrough)) or 0)


def _print_top_help(stream=None) -> None:
    out = stream or sys.stdout
    lines = [
        f"{APP_DISPLAY_NAME} {__version__}",
        "",
        f"Usage:  {APP_COMMAND} <tool> [args…]",
        f"        {APP_COMMAND}                   # interactive hub",
        f"        {APP_COMMAND} --help / --version",
        "",
        "Tools:",
    ]
    width = max(len(token) for token, _, _, _ in _TOOLS)
    for token, label, _, summary in _TOOLS:
        lines.append(f"  {token.ljust(width)}  {label}")
        lines.append(f"  {' ' * width}    {summary}")
    lines.extend([
        "",
        "Examples:",
        f"  {APP_COMMAND} codex clone-provider",
        f"  {APP_COMMAND} codex export-desktop-all --dry-run",
        f"  {APP_COMMAND} claude plan",
        f"  {APP_COMMAND} claude clean --preset safe --yes",
        "",
        "Backwards-compatible entry points still work:",
        "  codex-session-toolkit / cst   →  same as `aik codex …`",
        "  cc-clean                       →  same as `aik claude …`",
    ])
    out.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Interactive hub
# ---------------------------------------------------------------------------


def _run_hub() -> int:
    """Minimal picker: arrow keys + Enter to launch a tool's own TUI."""
    selected = 0
    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen + hide cursor
    sys.stdout.flush()
    try:
        while True:
            _render_hub(selected)
            key = read_key(timeout_ms=None if os.name != "nt" else 500)
            if key is None:
                continue
            if key in ("UP", "k", "K"):
                selected = (selected - 1) % len(_TOOLS)
                continue
            if key in ("DOWN", "j", "J"):
                selected = (selected + 1) % len(_TOOLS)
                continue
            if key == "ENTER":
                # Hand control over to the tool. Restore terminal so the
                # tool's own TUI starts from a known state.
                sys.stdout.write("\033[?25h\033[?1049l")
                sys.stdout.flush()
                tool_token = _TOOLS[selected][0]
                # Re-enter the alt screen via the tool's own setup; we just
                # exited so it can do that cleanly.
                return _dispatch_to_tool(tool_token, [])
            if isinstance(key, str) and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(_TOOLS):
                    sys.stdout.write("\033[?25h\033[?1049l")
                    sys.stdout.flush()
                    return _dispatch_to_tool(_TOOLS[idx][0], [])
                continue
            key_lower = (key or "").lower()
            if key_lower in {"q", "quit", "exit"} or key == "ESC":
                return 0
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def _render_hub(selected: int) -> None:
    pointer = glyphs().get("pointer", ">")
    title_lines = [
        style_text(f"{APP_DISPLAY_NAME} {__version__}", Ansi.BOLD, Ansi.BRIGHT_CYAN),
        style_text("选择一个工具进入它的交互菜单", Ansi.DIM),
    ]
    body_lines = []
    for idx, (token, label, _, summary) in enumerate(_TOOLS):
        prefix = (
            style_text(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
            if idx == selected
            else "  "
        )
        hotkey = style_text(f"[{idx + 1}]", Ansi.DIM)
        if idx == selected:
            body_lines.append(prefix + hotkey + " " + style_text(label, Ansi.BOLD, Ansi.UNDERLINE))
        else:
            body_lines.append(prefix + hotkey + " " + label)
        body_lines.append("      " + style_text(summary, Ansi.DIM))
    footer_lines = [
        style_text(
            "↑/↓ 选择  |  Enter / 1-9 进入  |  q/Esc 退出",
            Ansi.DIM,
        ),
    ]

    clear_screen()
    sys.stdout.write("\033[H")  # home cursor before drawing
    for line in title_lines:
        sys.stdout.write(line + "\n")
    sys.stdout.write("\n")
    for line in render_box(body_lines, width=72, border_codes=(Ansi.DIM, Ansi.BLUE)):
        sys.stdout.write(line + "\n")
    sys.stdout.write("\n")
    for line in render_box(footer_lines, width=72, border_codes=(Ansi.DIM, Ansi.BLUE)):
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
