"""Claude-specific terminal helpers (CC CLEAN logo + layout cap).

Tool-agnostic primitives — Ansi, color detection, Windows VT enable,
``clear_screen``, ``read_key``, ``render_box``, glyph tables, display-width
math, ``term_width`` / ``term_height``, ``configure_text_streams`` — live in
:mod:`ai_cli_kit.core.tui.terminal` and are re-exported below for backwards
compatibility. Every ``from ..tui.terminal import …`` call site keeps working
unchanged, **including the Windows VT bootstrap that core runs at import
time** — which is the bit that was previously missing from this duplicate
module and silently broke the TUI on legacy Windows cmd.exe consoles.

What stays Claude-specific in this module:
* ``LOGO_FONT_BANNER`` — the 5-row pixel font used by the CC CLEAN wordmark.
* ``_render_wordmark`` family — gradient/shadow composer.
* ``app_logo_lines`` — produces the "CC CLEAN" banner sized to the terminal.
* ``tui_width`` — Claude's layout cap honouring ``CCC_TUI_MAX_WIDTH`` first,
  then ``CST_TUI_MAX_WIDTH`` / ``CSC_TUI_MAX_WIDTH`` so users sharing a tool
  config get a single layout cap across both Codex and Claude.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# Re-export every tool-agnostic primitive so existing call sites keep working
# AND get the Windows VT-mode bootstrap that core runs at module import.
from ...core.tui.terminal import (  # noqa: F401
    ANSI_ESCAPE_RE,
    ASCII_BOX_CHARS,
    ASCII_GLYPHS,
    ASCII_UI_ENV_NAMES,
    Ansi,
    COLOR_ENABLED,
    UNICODE_BOX_CHARS,
    UNICODE_GLYPHS,
    _box_chars,
    _can_encode,
    _env_first,
    _take_prefix_by_width,
    _take_suffix_by_width,
    align_line,
    clear_screen,
    configure_text_streams,
    display_width,
    ellipsize_middle,
    env_first,
    glyphs,
    is_interactive_terminal,
    pad_right,
    read_key,
    render_box,
    strip_ansi,
    style_text,
    supports_color,
    term_height,
    term_width,
)

# Wordmark font + renderer are shared with codex/aik via core.tui.wordmark.
from ...core.tui.wordmark import (  # noqa: F401
    LOGO_FONT_3X7,
    LOGO_FONT_4X5,
    LOGO_FONT_4X7,
    LOGO_FONT_BANNER,
    render_wordmark as _render_wordmark,
)


# ---------------------------------------------------------------------------
# Claude-specific layout cap (CCC_* takes priority over CST_* / CSC_*).
# ---------------------------------------------------------------------------


def tui_width(cols: Optional[int] = None, *, fallback: int = 90) -> int:
    """Return the effective inner width Claude menus should target."""
    cols = term_width(fallback=fallback) if cols is None else int(cols)
    if cols <= 0:
        cols = fallback

    width = cols
    if cols >= 24:
        width = max(24, cols - 2)

    cap = env_first("CCC_TUI_MAX_WIDTH", "CST_TUI_MAX_WIDTH", "CSC_TUI_MAX_WIDTH")
    if cap:
        try:
            cap_n = int(cap)
            if cap_n > 0:
                width = min(width, max(24, cap_n))
        except Exception:
            pass

    return max(20, width)



def app_logo_lines(max_width: Optional[int] = None) -> List[str]:
    max_width = term_width() if max_width is None else max(20, int(max_width))

    ascii_ui = bool(env_first(*ASCII_UI_ENV_NAMES))
    if not ascii_ui and not _can_encode("█"):
        ascii_ui = True

    fill = "#" if ascii_ui else "█"
    shadow = "." if ascii_ui else ("░" if _can_encode("░") else " ")

    def _normalize(lines: List[str]) -> List[str]:
        if not lines:
            return lines
        block_width = max((display_width(line) for line in lines), default=0)
        return [pad_right(line, block_width) for line in lines]

    def _max_width(lines: List[str]) -> int:
        return max((display_width(line) for line in lines), default=0)

    brand_specs = (
        {"text": "CC CLEAN", "font": LOGO_FONT_4X7, "char_gap": 1, "word_gap": 2, "shadow_ok": True},
        {"text": "CC CLEAN", "font": LOGO_FONT_4X5, "char_gap": 1, "word_gap": 2, "shadow_ok": True},
        {"text": "CC CLEAN", "font": LOGO_FONT_3X7, "char_gap": 0, "word_gap": 1, "shadow_ok": False},
    )
    for spec in brand_specs:
        rendered = _render_wordmark(
            spec["text"],
            font=spec["font"],
            fill=fill,
            shadow=shadow,
            max_width=max_width,
            char_gap=spec["char_gap"],
            word_gap=spec["word_gap"],
            shadow_ok=spec["shadow_ok"],
            gradient=("#00FFFF", "#0048FF"),
        )
        if _max_width(rendered) <= max_width:
            short = "cc-clean"
            if COLOR_ENABLED:
                short = style_text("cc", Ansi.BOLD, Ansi.BRIGHT_CYAN) + style_text("-", Ansi.DIM) + style_text(
                    "clean",
                    Ansi.BOLD,
                    Ansi.BRIGHT_BLUE,
                )
            return _normalize(rendered) + [ellipsize_middle(short, max_width)]

    fallback = _render_wordmark(
        "CC",
        font=LOGO_FONT_4X5,
        fill=fill,
        shadow=shadow,
        max_width=max_width,
        char_gap=1,
        word_gap=2,
        shadow_ok=True,
        gradient=("#00FFFF", "#0048FF"),
    )
    return _normalize(fallback) + [ellipsize_middle("cc-clean", max_width)]
