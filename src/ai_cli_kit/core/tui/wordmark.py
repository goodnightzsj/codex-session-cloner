"""Shared pixel-art wordmark renderer.

Every sub-tool (and the top-level ``aik`` hub) renders its banner with the
same gradient/shadow composer extracted from the original codex/claude
duplicates so all three brand markers look like they belong to one product.

A single ``LOGO_FONT_BANNER`` covers the union of letters used by current
brand strings:

* Codex Session Toolkit  → C O D E X S I N T R K L
* CC Clean (Claude Code) → C L E A N
* AI CLI KIT             → A I C L K T

Adding a new sibling tool? Either the letter is already in the font, or
add another 5-row pattern below — the renderer needs no other changes.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .terminal import Ansi, COLOR_ENABLED, display_width, style_text


# 5-row pixel font. Each glyph is a list[str] of length 5; ``X`` cells get
# replaced with ``fill`` at render time (kept literal so the source diff is
# easy to read).
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
    "O": [
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ],
    "R": [
        "████ ",
        "█   █",
        "████ ",
        "█  █ ",
        "█   █",
    ],
    "S": [
        " ████",
        "█    ",
        " ███ ",
        "    █",
        "████ ",
    ],
    "T": [
        "█████",
        "  █  ",
        "  █  ",
        "  █  ",
        "  █  ",
    ],
    "X": [
        "█   █",
        " █ █ ",
        "  █  ",
        " █ █ ",
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

# Backwards-compat aliases — the codex/claude logo composers used 4×5/4×7/3×7
# size hints; our font is 5-row and the composer ignores the size suffix.
LOGO_FONT_4X5 = LOGO_FONT_BANNER
LOGO_FONT_4X7 = LOGO_FONT_BANNER
LOGO_FONT_3X7 = LOGO_FONT_BANNER


# ---------------------------------------------------------------------------
# Rendering primitives (private — public surface is ``render_wordmark``)
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_wordmark(
    text: str,
    *,
    font: dict = LOGO_FONT_BANNER,
    fill: str = "█",
    shadow: str = "░",
    max_width: int = 90,
    char_gap: int = 1,
    word_gap: int = 2,
    shadow_ok: bool = True,
    fill_codes: Tuple[str, ...] = (Ansi.BOLD, Ansi.BRIGHT_CYAN),
    shadow_codes: Tuple[str, ...] = (Ansi.DIM, Ansi.BRIGHT_BLUE),
    gradient: Optional[Tuple[str, str]] = None,
) -> List[str]:
    """Render ``text`` as a coloured pixel-art wordmark with optional shadow.

    * ``font`` defaults to :data:`LOGO_FONT_BANNER` (covers Codex / Claude /
      AIK letter sets) — pass a custom dict for bespoke fonts.
    * ``fill`` / ``shadow`` are the literal pixel chars; pass ASCII (``#``
      / ``.``) on terminals that can't encode block glyphs.
    * ``shadow_ok`` toggles the offset shadow (set False if the wordmark is
      already wide enough that a shadow would push past ``max_width``).
    * ``gradient`` (``(start_hex, end_hex)``) overrides ``fill_codes`` with
      a per-column 24-bit RGB gradient — only honoured when terminal colour
      support is detected.
    """
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


__all__ = [
    "LOGO_FONT_3X7",
    "LOGO_FONT_4X5",
    "LOGO_FONT_4X7",
    "LOGO_FONT_BANNER",
    "render_wordmark",
]
