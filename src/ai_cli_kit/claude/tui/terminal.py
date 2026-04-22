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


# ---------------------------------------------------------------------------
# CC CLEAN logo wordmark — bespoke 5-row pixel font (subset of the alphabet
# we actually need: A C E L N).
# ---------------------------------------------------------------------------


LOGO_FONT_BANNER = {
    "A": [
        " ███ ",
        "█   █",
        "█████",
        "█   █",
        "█   █",
    ],
    "C": [
        " ████",
        "█    ",
        "█    ",
        "█    ",
        " ████",
    ],
    "E": [
        "█████",
        "█    ",
        "███  ",
        "█    ",
        "█████",
    ],
    "L": [
        "█    ",
        "█    ",
        "█    ",
        "█    ",
        "█████",
    ],
    "N": [
        "█   █",
        "██  █",
        "█ █ █",
        "█  ██",
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


# ---------------------------------------------------------------------------
# CC CLEAN logo renderer (gradient/shadow wordmark composer)
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
            for index in range(height):
                rows[index] += " " * word_gap
            continue

        pattern = font.get(ch.upper())
        if pattern is None:
            pattern = [(" " * fallback_width) for _ in range(height)]
            pattern[height // 2] = (ch + (" " * fallback_width))[:fallback_width]

        for index in range(height):
            rows[index] += pattern[index] + (" " * char_gap)

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
    source = [line.ljust(width) for line in lines]

    out_height = height + (1 if extend_height else 0)
    out_width = width + (1 if extend_width else 0)
    out = [list(" " * out_width) for _ in range(out_height)]

    for row in range(height):
        for col in range(width):
            if source[row][col] == fill:
                out[row][col] = fill

    min_row, max_row = height, -1
    min_col, max_col = width, -1
    for row in range(height):
        for col in range(width):
            if source[row][col] != fill:
                continue
            min_row = min(min_row, row)
            max_row = max(max_row, row)
            min_col = min(min_col, col)
            max_col = max(max_col, col)
    if max_row < 0 or max_col < 0:
        return lines

    for row in range(height):
        for col in range(width):
            if source[row][col] != fill:
                continue
            shadow_row = row + 1
            shadow_col = col + 1
            if shadow_row <= max_row and shadow_col <= max_col:
                continue
            if shadow_row < out_height and shadow_col < out_width and out[shadow_row][shadow_col] == " ":
                out[shadow_row][shadow_col] = shadow

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
    return tuple(int(hex_color[index:index + 2], 16) for index in (0, 2, 4))


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
            for index, char in enumerate(line):
                if char == fill:
                    ratio = index / max(1, line_len - 1)
                    red = int(r1 + (r2 - r1) * ratio)
                    green = int(g1 + (g2 - g1) * ratio)
                    blue = int(b1 + (b2 - b1) * ratio)
                    rendered.append(f"\033[38;2;{red};{green};{blue}m{fill}\033[0m")
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
