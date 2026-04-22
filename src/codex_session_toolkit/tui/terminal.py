"""Codex-specific terminal helpers (logo + layout sugar).

Tool-agnostic primitives (Ansi, color detection, box drawing, glyph tables,
display-width math, ``term_width`` / ``term_height``, ``read_key``,
``render_box``, ``clear_screen``, etc.) live in
``codex_session_toolkit.core.tui.terminal`` and are re-exported below for
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
from ..core.tui.terminal import (  # noqa: F401
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
# Codex logo wordmark — bespoke 5-row pixel font shared across banners
# ---------------------------------------------------------------------------


LOGO_FONT_BANNER = {
    "C": [
        " ████",
        "█    ",
        "█    ",
        "█    ",
        " ████",
    ],
    "O": [
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ],
    "D": [
        "████ ",
        "█   █",
        "█   █",
        "█   █",
        "████ ",
    ],
    "E": [
        "█████",
        "█    ",
        "███  ",
        "█    ",
        "█████",
    ],
    "X": [
        "█   █",
        " █ █ ",
        "  █  ",
        " █ █ ",
        "█   █",
    ],
    "S": [
        " ████",
        "█    ",
        " ███ ",
        "    █",
        "████ ",
    ],
    "I": [
        "█████",
        "  █  ",
        "  █  ",
        "  █  ",
        "█████",
    ],
    "K": [
        "█   █",
        "█  █ ",
        "███  ",
        "█  █ ",
        "█   █",
    ],
    "N": [
        "█   █",
        "██  █",
        "█ █ █",
        "█  ██",
        "█   █",
    ],
    "L": [
        "█    ",
        "█    ",
        "█    ",
        "█    ",
        "█████",
    ],
    "T": [
        "█████",
        "  █  ",
        "  █  ",
        "  █  ",
        "  █  ",
    ],
    "R": [
        "████ ",
        "█   █",
        "████ ",
        "█  █ ",
        "█   █",
    ],
    "-": [
        "     ",
        "     ",
        "█████",
        "     ",
        "     ",
    ],
    " ": [
        "  ",
        "  ",
        "  ",
        "  ",
        "  ",
    ],
}

LOGO_FONT_4X5 = LOGO_FONT_BANNER
LOGO_FONT_4X7 = LOGO_FONT_BANNER
LOGO_FONT_3X7 = LOGO_FONT_BANNER


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


# ---------------------------------------------------------------------------
# Codex logo renderer (gradient/shadow wordmark composer)
# ---------------------------------------------------------------------------


def _render_logo_text(
    text: str,
    *,
    font: dict,
    fill: str,
    char_gap: int,
    word_gap: int,
) -> List[str]:
    patterns = list(font.values())
    height = len(patterns[0]) if patterns else 0
    fallback_width = max((len(row) for pattern in patterns for row in pattern), default=4)
    rows = [""] * height

    for ch in text:
        if ch == " ":
            for i in range(height):
                rows[i] += " " * word_gap
            continue

        pattern = font.get(ch.upper())
        if pattern is None:
            pattern = [(" " * fallback_width) for _ in range(height)]
            pattern[height // 2] = (ch + (" " * fallback_width))[:fallback_width]

        for i in range(height):
            rows[i] += pattern[i] + (" " * char_gap)

    return [row.replace("X", fill).rstrip() for row in rows]


def _apply_logo_shadow(
    lines: List[str],
    *,
    fill: str,
    shadow: str,
    extend_width: bool,
    extend_height: bool,
) -> List[str]:
    if not lines or not shadow or shadow == " ":
        return lines

    height = len(lines)
    width = max((len(line) for line in lines), default=0)
    src = [line.ljust(width) for line in lines]

    out_height = height + (1 if extend_height else 0)
    out_width = width + (1 if extend_width else 0)
    out = [list(" " * out_width) for _ in range(out_height)]

    for r in range(height):
        for c in range(width):
            if src[r][c] == fill:
                out[r][c] = fill

    min_r, max_r = height, -1
    min_c, max_c = width, -1
    for r in range(height):
        for c in range(width):
            if src[r][c] != fill:
                continue
            min_r = min(min_r, r)
            max_r = max(max_r, r)
            min_c = min(min_c, c)
            max_c = max(max_c, c)
    if max_r < 0 or max_c < 0:
        return lines

    for r in range(height):
        for c in range(width):
            if src[r][c] != fill:
                continue
            rr = r + 1
            cc = c + 1
            if rr <= max_r and cc <= max_c:
                continue
            if rr < out_height and cc < out_width and out[rr][cc] == " ":
                out[rr][cc] = shadow

    return ["".join(row).rstrip() for row in out]


def _style_logo_chars(
    lines: List[str],
    *,
    fill: str,
    shadow: str,
    fill_codes: Tuple[str, ...] = (Ansi.BOLD, Ansi.BRIGHT_CYAN),
    shadow_codes: Tuple[str, ...] = (Ansi.DIM, Ansi.BRIGHT_BLUE),
) -> List[str]:
    if not COLOR_ENABLED:
        return lines

    shadow_token = style_text(shadow, *shadow_codes) if shadow and shadow != " " and shadow_codes else None
    fill_token = style_text(fill, *fill_codes) if fill_codes else fill

    out: List[str] = []
    for line in lines:
        processed = line
        if shadow_token:
            processed = processed.replace(shadow, shadow_token)
        processed = processed.replace(fill, fill_token)
        out.append(processed)
    return out


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _render_wordmark(
    text: str,
    *,
    font: dict,
    fill: str,
    shadow: str,
    max_width: int,
    char_gap: int,
    word_gap: int,
    shadow_ok: bool,
    fill_codes: Tuple[str, ...] = (Ansi.BOLD, Ansi.BRIGHT_CYAN),
    shadow_codes: Tuple[str, ...] = (Ansi.DIM, Ansi.BRIGHT_BLUE),
    gradient: Optional[Tuple[str, str]] = None,
) -> List[str]:
    base = _render_logo_text(
        text,
        font=font,
        fill=fill,
        char_gap=char_gap,
        word_gap=word_gap,
    )
    base_width = max((display_width(line) for line in base), default=0)
    shadow_char = shadow if shadow_ok else " "
    extend_width = shadow_char != " " and (base_width + 1 <= max_width)
    with_shadow = _apply_logo_shadow(
        base,
        fill=fill,
        shadow=shadow_char,
        extend_width=extend_width,
        extend_height=(shadow_char != " "),
    )

    if gradient and COLOR_ENABLED:
        out = []
        start_hex, end_hex = gradient
        shadow_token = style_text(shadow_char, *shadow_codes) if shadow_char != " " else " "
        r1, g1, b1 = _hex_to_rgb(start_hex)
        r2, g2, b2 = _hex_to_rgb(end_hex)

        for line in with_shadow:
            line_len = len(line)
            rendered = []
            for i, char in enumerate(line):
                if char == fill:
                    t = i / max(1, line_len - 1)
                    r = int(r1 + (r2 - r1) * t)
                    g = int(g1 + (g2 - g1) * t)
                    b = int(b1 + (b2 - b1) * t)
                    rendered.append(f"\033[38;2;{r};{g};{b}m{fill}\033[0m")
                elif char == shadow_char:
                    rendered.append(shadow_token)
                else:
                    rendered.append(char)
            out.append("".join(rendered))
        return out

    return _style_logo_chars(
        with_shadow,
        fill=fill,
        shadow=shadow_char,
        fill_codes=fill_codes,
        shadow_codes=shadow_codes,
    )


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
