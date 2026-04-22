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

from functools import lru_cache

from . import APP_COMMAND, APP_DISPLAY_NAME, __version__
from .core.tui.terminal import (
    ASCII_UI_ENV_NAMES,
    Ansi,
    COLOR_ENABLED,
    _can_encode,
    _WINDOWS_VT_OK,
    clear_screen,
    configure_text_streams,
    display_width,
    env_first,
    glyphs,
    is_interactive_terminal,
    read_key,
    render_box,
    style_text,
    term_width,
)
from .core.tui.wordmark import LOGO_FONT_BANNER, render_wordmark


# Tool registry — pairs the public token (``codex`` / ``claude``) with the
# entry point function and the hub display copy. Adding a new sibling tool
# means appending one tuple here plus its launcher script.
_TOOLS = (
    ("codex", "Codex Session Toolkit", "ai_cli_kit.codex.cli", "克隆 / 导出 / 导入 / 修复 Codex 会话"),
    ("claude", "CC Clean (Claude Code)", "ai_cli_kit.claude.cli", "清理本地标识 / 遥测 / 历史，安全备份"),
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
    """Two-card picker: arrow keys / number / Enter to launch a tool's own TUI.

    Designed as the "front door" — pixel-art logo, two big highlighted cards,
    one-key entry. After a sub-tool exits we return to the hub so the user
    can pick another tool or quit cleanly with Esc / q. Only an explicit
    Esc / q at the hub terminates the whole process.
    """
    selected = 0
    # Only emit VT control sequences (alt screen + cursor toggle) when the
    # terminal can actually interpret them. On legacy Windows cmd.exe with
    # ``_WINDOWS_VT_OK=False`` they would print as literal ``\033[?1049h``
    # text and corrupt the UI. In that case we fall back to ``clear_screen``
    # (which itself uses ``os.system("cls")`` on legacy consoles) and skip
    # cursor hiding — the experience is uglier (no alt-screen, no cursor
    # hide) but at least readable.
    if _WINDOWS_VT_OK:
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
    else:
        clear_screen()
    last_signature: Optional[tuple] = None
    try:
        while True:
            signature = (selected, term_width())
            if signature != last_signature:
                _render_hub(selected)
                last_signature = signature
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
                _enter_tool(_TOOLS[selected][0])
                last_signature = None  # force redraw of hub
                continue
            if isinstance(key, str) and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(_TOOLS):
                    _enter_tool(_TOOLS[idx][0])
                    last_signature = None
                continue
            # Esc, q, Q, "exit", "quit" — anything that means "I'm done with
            # the whole toolbox" — exits the process.
            key_lower = (key or "").lower() if isinstance(key, str) else ""
            if key == "ESC" or key_lower in {"q", "quit", "exit"}:
                return 0
    finally:
        # Symmetric with the entry guard: only undo what we actually emitted.
        if _WINDOWS_VT_OK:
            sys.stdout.write("\033[?25h\033[?1049l")
        else:
            clear_screen()
        sys.stdout.flush()


def _enter_tool(tool_token: str) -> None:
    """Hand control to the sub-tool, sharing the hub's alt-screen surface.

    Setting ``AIK_HUB_ACTIVE=1`` tells codex/claude TUIs to skip their own
    ``\\033[?1049h`` / ``\\033[?1049l`` toggles — they'd otherwise flash the
    outer shell between transitions. Hub stays in alt screen the whole time;
    sub-tool just clears + renders, then clears on exit so the hub can redraw.
    """
    # clear + show cursor for sub-tool prompts. clear_screen() handles the
    # legacy-Windows fallback (os.system("cls")) when VT is unavailable;
    # the ``\033[?25h`` cursor-show is VT-only and would print garbage on
    # legacy cmd.exe, so guard it.
    clear_screen()
    if _WINDOWS_VT_OK:
        sys.stdout.write("\033[?25h")
    sys.stdout.flush()
    os.environ["AIK_HUB_ACTIVE"] = "1"
    try:
        _dispatch_to_tool(tool_token, [])
    finally:
        os.environ.pop("AIK_HUB_ACTIVE", None)
        if _WINDOWS_VT_OK:
            sys.stdout.write("\033[?25l")  # rehide cursor for hub
            sys.stdout.flush()


def _render_hub(selected: int) -> None:
    pointer = glyphs().get("pointer", ">")
    cols = term_width()
    card_width = max(40, min(cols - 4, 90))

    clear_screen()
    # ``\033[H`` is redundant — clear_screen() already homes the cursor
    # via either ``\033[2J\033[H`` (VT) or ``os.system("cls")`` (legacy
    # Windows). Emitting it raw on a non-VT console would print garbage,
    # so only do it where VT is known to work.
    if _WINDOWS_VT_OK:
        sys.stdout.write("\033[H")
    sys.stdout.write("\n")

    # Pixel-art banner — uses the same wordmark composer as codex/claude so
    # all three TUIs feel like one product. The banner adapts to terminal
    # width: full "AI CLI KIT" when room exists, tighter "AIK" otherwise.
    #
    # Center the WHOLE block by the longest row's width, NOT each row by
    # its own width. Per-row centering caused a visible stagger where
    # shorter rows drifted right relative to longer ones, making the
    # logo unreadable as a single wordmark.
    logo_lines = _aik_logo_lines(cols)
    block_width = max((display_width(line) for line in logo_lines), default=0)
    block_pad = max(0, (cols - block_width) // 2)
    block_indent = " " * block_pad
    for line in logo_lines:
        sys.stdout.write(block_indent + line + "\n")
    sys.stdout.write("\n")
    sys.stdout.write(_centered(
        style_text(f"{APP_DISPLAY_NAME} v{__version__}  ·  统一 AI CLI 工具箱", Ansi.DIM),
        cols,
    ) + "\n")
    sys.stdout.write("\n")

    # Two big cards — selected one gets BRIGHT_CYAN+BOLD border + bright label,
    # the other dims down so the active choice is unmistakable at a glance.
    # Each card is centred horizontally as a UNIT (pad = (cols - card_width
    # - marker_width) / 2) so the cards line up with the centred banner /
    # subtitle above and match the sub-tools' centred layout.
    marker_width = 2  # "  " or "› " — both render as 2 columns
    card_pad = max(0, (cols - card_width - marker_width) // 2)
    card_indent = " " * card_pad

    for idx, (_token, label, _module, summary) in enumerate(_TOOLS):
        is_selected = idx == selected
        if is_selected:
            border_codes = (Ansi.BOLD, Ansi.BRIGHT_CYAN)
            number = style_text(f"{idx + 1}", Ansi.BOLD, Ansi.BRIGHT_CYAN)
            label_styled = style_text(label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.BRIGHT_CYAN)
            summary_styled = style_text(summary, Ansi.BRIGHT_BLUE)
        else:
            border_codes = (Ansi.DIM,)
            number = style_text(f"{idx + 1}", Ansi.DIM)
            label_styled = style_text(label, Ansi.DIM)
            summary_styled = style_text(summary, Ansi.DIM)

        card_lines = [
            f"  {number}.  {label_styled}",
            f"      {summary_styled}",
        ]
        rendered = render_box(card_lines, width=card_width, border_codes=border_codes)

        prefix_active = style_text(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
        prefix_idle = "  "
        for line_idx, line in enumerate(rendered):
            marker = prefix_active if (is_selected and line_idx == 1) else prefix_idle
            sys.stdout.write(card_indent + marker + line + "\n")
        sys.stdout.write("\n")

    footer = style_text(
        "↑↓ 选择    Enter / 数字键 进入    Esc / q 退出",
        Ansi.DIM,
    )
    sys.stdout.write(_centered(footer, cols) + "\n")
    sys.stdout.flush()


def _centered(text: str, cols: int) -> str:
    width = display_width(text)
    pad = max(0, (cols - width) // 2)
    return (" " * pad) + text


@lru_cache(maxsize=8)
def _aik_logo_lines(cols: int) -> tuple:
    """Return the AI CLI KIT pixel-art logo sized to ``cols``.

    Three deliberate choices for hub readability:

    * ``word_gap=4`` — wide visual gutter so users see *three* words
      "AI · CLI · KIT" instead of one continuous blob.
    * ``shadow_ok=False`` — no diagonal shadow row. The shadow row sits
      one column right of the letters (because shadows extend down-right),
      which made the bottom of the banner look misaligned. Plain letters
      without shadow stack cleanly.
    * Falls back to compact "AIK" when even the tighter "AI CLI KIT" can't
      fit the terminal width.

    Output is cached per (cols, color, ascii) so hub redraws don't
    re-rasterise on every keystroke.
    """
    ascii_ui = bool(env_first(*ASCII_UI_ENV_NAMES)) or not _can_encode("█")
    fill = "#" if ascii_ui else "█"

    max_width = max(20, cols - 4)
    gradient = ("#00FFFF", "#0048FF") if COLOR_ENABLED else None

    for text, char_gap, word_gap in (
        ("AI CLI KIT", 1, 4),
        ("AI CLI KIT", 1, 3),
        ("AI CLI KIT", 0, 2),
        ("AIK", 1, 0),
    ):
        rendered = render_wordmark(
            text,
            font=LOGO_FONT_BANNER,
            fill=fill,
            shadow=" ",
            max_width=max_width,
            char_gap=char_gap,
            word_gap=word_gap,
            shadow_ok=False,
            gradient=gradient,
        )
        if max((display_width(line) for line in rendered), default=0) <= max_width:
            return tuple(rendered)
    return tuple(rendered)


if __name__ == "__main__":
    raise SystemExit(main())
