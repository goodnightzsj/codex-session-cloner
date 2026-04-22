"""Codex-specific terminal helpers (logo + layout sugar).

Tool-agnostic primitives (Ansi, color detection, box drawing, glyph tables,
display-width math, ``term_width`` / ``term_height``, ``read_key``,
``render_box``, ``clear_screen``, etc.) live in
``ai_cli_kit.codex.core.tui.terminal`` and are re-exported below for
backwards compatibility — every ``from .terminal import …`` site keeps working
without changes.

What stays Codex-specific in this module:
* ``LOGO_FONT_BANNER`` and the wordmark/gradient renderer for the Codex logo.
* ``app_logo_lines`` (memoised composite) — produces the "CODEX SESSION TOOLKIT"
  banner sized to the current terminal.
* ``tui_width`` — Codex layout cap reading ``CST_TUI_MAX_WIDTH`` / ``CSC_TUI_MAX_WIDTH``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional, Tuple

# Re-export every tool-agnostic primitive so existing call sites keep working.
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
    horizontal_rule,
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


# ---------------------------------------------------------------------------
# Wordmark font + renderer are shared with claude/aik via core.tui.wordmark.
# Re-exported here so existing codex call sites (``LOGO_FONT_BANNER``,
# ``_render_wordmark``) keep working without churn.
# ---------------------------------------------------------------------------


from ...core.tui.wordmark import (  # noqa: F401,E402
    LOGO_FONT_3X7,
    LOGO_FONT_4X5,
    LOGO_FONT_4X7,
    LOGO_FONT_BANNER,
    render_wordmark as _render_wordmark,
)


# ---------------------------------------------------------------------------
# Codex-specific layout cap (different tools want different max widths)
# ---------------------------------------------------------------------------


def tui_width(cols: Optional[int] = None, *, fallback: int = 90) -> int:
    """Return the effective inner width Codex menus should target.

    Honors ``CST_TUI_MAX_WIDTH`` / ``CSC_TUI_MAX_WIDTH`` so a user can cap the
    UI at a comfortable reading width on ultrawide screens.
    """
    cols = term_width(fallback=fallback) if cols is None else int(cols)
    if cols <= 0:
        cols = fallback

    width = cols
    if cols >= 24:
        width = max(24, cols - 2)

    cap = env_first("CST_TUI_MAX_WIDTH", "CSC_TUI_MAX_WIDTH")
    if cap:
        try:
            cap_n = int(cap)
            if cap_n > 0:
                width = min(width, max(24, cap_n))
        except Exception:
            pass

    return max(20, width)



def app_logo_lines(max_width: Optional[int] = None) -> List[str]:
    resolved_width = term_width() if max_width is None else max(20, int(max_width))
    return list(_app_logo_lines_cached(resolved_width, COLOR_ENABLED, _ascii_ui_active()))


def _ascii_ui_active() -> bool:
    return bool(env_first(*ASCII_UI_ENV_NAMES))


@lru_cache(maxsize=32)
def _app_logo_lines_cached(max_width: int, color_enabled: bool, ascii_ui_env: bool) -> Tuple[str, ...]:
    return tuple(_compute_app_logo_lines(max_width))


def _compute_app_logo_lines(max_width: int) -> List[str]:
    max_width = max(20, int(max_width))

    ascii_ui = bool(env_first(*ASCII_UI_ENV_NAMES))
    if not ascii_ui and not _can_encode("█"):
        ascii_ui = True

    fill = "#" if ascii_ui else "█"
    shadow = "." if ascii_ui else ("░" if _can_encode("░") else " ")

    def _normalize_logo_block(lines: List[str]) -> List[str]:
        if not lines:
            return lines
        block_width = max((display_width(line) for line in lines), default=0)
        return [pad_right(line, block_width) for line in lines]

    def _max_w(lines: List[str]) -> int:
        return max((display_width(line) for line in lines), default=0)

    def _merge_horiz(left: List[str], right: List[str], *, gap: int) -> List[str]:
        left = _normalize_logo_block(left)
        right = _normalize_logo_block(right)
        lw = _max_w(left)
        rw = _max_w(right)
        height = max(len(left), len(right))
        left = left + [pad_right("", lw)] * (height - len(left))
        right = right + [pad_right("", rw)] * (height - len(right))
        spacer = " " * max(0, int(gap))
        return [l + spacer + r for l, r in zip(left, right)]

    def _ideal_part_gap(*, min_gap: int) -> int:
        return max(min_gap, min(18, max_width // 20))

    def _render_parts(font: dict, *, char_gap: int) -> Tuple[List[str], List[str], List[str]]:
        return (
            _render_wordmark(
                "CODEX",
                font=font,
                fill=fill,
                shadow=shadow,
                max_width=max_width,
                char_gap=char_gap,
                word_gap=0,
                shadow_ok=True,
                fill_codes=(),
                shadow_codes=(Ansi.DIM, Ansi.BLUE),
                gradient=("#00FFFF", "#0088FF"),
            ),
            _render_wordmark(
                "SESSION",
                font=font,
                fill=fill,
                shadow=shadow,
                max_width=max_width,
                char_gap=char_gap,
                word_gap=0,
                shadow_ok=True,
                fill_codes=(),
                shadow_codes=(Ansi.DIM, Ansi.MAGENTA),
                gradient=("#FF00FF", "#8800FF"),
            ),
            _render_wordmark(
                "TOOLKIT",
                font=font,
                fill=fill,
                shadow=shadow,
                max_width=max_width,
                char_gap=char_gap,
                word_gap=0,
                shadow_ok=True,
                fill_codes=(),
                shadow_codes=(Ansi.DIM, Ansi.BLUE),
                gradient=("#0088FF", "#0000FF"),
            ),
        )

    def _try_triple_line(font: dict, *, char_gap: int, min_gap: int) -> Optional[List[str]]:
        codex, session, toolkit = _render_parts(font, char_gap=char_gap)
        base_sum = _max_w(codex) + _max_w(session) + _max_w(toolkit)
        max_gap = (max_width - base_sum) // 2
        if max_gap < min_gap:
            return None
        part_gap = min(max_gap, _ideal_part_gap(min_gap=min_gap))
        line = _merge_horiz(_merge_horiz(codex, session, gap=part_gap), toolkit, gap=part_gap)
        if _max_w(line) <= max_width:
            return _normalize_logo_block(line)
        return None

    def _try_stacked(font: dict, *, char_gap: int, min_gap: int) -> Optional[List[str]]:
        codex, session, toolkit = _render_parts(font, char_gap=char_gap)

        bottom_base = _max_w(session) + _max_w(toolkit)
        bottom_max_gap = max_width - bottom_base
        if bottom_max_gap >= min_gap and _max_w(codex) <= max_width:
            bottom_gap = min(bottom_max_gap, _ideal_part_gap(min_gap=min_gap))
            bottom = _merge_horiz(session, toolkit, gap=bottom_gap)
            stacked = _normalize_logo_block(codex) + _normalize_logo_block(bottom)
            if _max_w(stacked) <= max_width:
                return stacked

        if _max_w(codex) <= max_width and _max_w(session) <= max_width and _max_w(toolkit) <= max_width:
            stacked = _normalize_logo_block(codex) + _normalize_logo_block(session) + _normalize_logo_block(toolkit)
            if _max_w(stacked) <= max_width:
                return stacked

        return None

    for font, char_gap in (
        (LOGO_FONT_4X7, 1),
        (LOGO_FONT_4X5, 1),
        (LOGO_FONT_3X7, 1),
        (LOGO_FONT_4X7, 0),
        (LOGO_FONT_4X5, 0),
        (LOGO_FONT_3X7, 0),
    ):
        candidate = _try_triple_line(font, char_gap=char_gap, min_gap=2)
        if candidate:
            return candidate

    for font in (LOGO_FONT_4X5, LOGO_FONT_3X7):
        for char_gap in (1, 0):
            candidate = _try_stacked(font, char_gap=char_gap, min_gap=2)
            if candidate:
                return candidate

    full_text = "CODEX SESSION TOOLKIT"
    for spec in ({"char_gap": 0, "word_gap": 1}, {"char_gap": 0, "word_gap": 0}):
        full = _render_wordmark(
            full_text,
            font=LOGO_FONT_3X7,
            fill=fill,
            shadow=shadow,
            max_width=max_width,
            fill_codes=(Ansi.BOLD, Ansi.BRIGHT_CYAN),
            shadow_codes=(),
            shadow_ok=False,
            gradient=None,
            **spec,
        )
        if _max_w(full) <= max_width:
            return _normalize_logo_block(full)

    acronym = _render_wordmark(
        "CST",
        font=LOGO_FONT_3X7,
        fill=fill,
        shadow=shadow,
        max_width=max_width,
        char_gap=1,
        word_gap=2,
        shadow_ok=True,
        gradient=("#00FFFF", "#0000FF"),
    )
    short = "codex-session-toolkit"
    segments = short.split("-")
    if COLOR_ENABLED and len(segments) == 3:
        seg_colors = (Ansi.BRIGHT_CYAN, Ansi.BRIGHT_MAGENTA, Ansi.BRIGHT_BLUE)
        dash = style_text("-", Ansi.DIM)
        short_line = dash.join(style_text(seg, Ansi.BOLD, color) for seg, color in zip(segments, seg_colors))
    else:
        short_line = short
    return _normalize_logo_block(acronym) + [ellipsize_middle(short_line, max_width)]
