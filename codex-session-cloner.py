"""
Safely clone Codex sessions to the currently configured provider.
- Idempotent
- Dry-run supported
- Cleans up legacy unmarked clones
- Interactive TUI by default (no args)
"""

import os
import json
import uuid
import argparse
import re
import sys
import unicodedata
import shutil
import ast
import platform
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

# Configuration
SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
CONFIG_FILE = os.path.expanduser("~/.codex/config.toml")
DEFAULT_PROVIDER = "cliproxyapi"

class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    REVERSE = "\033[7m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term.lower() == "dumb":
        return False
    return True

_COLOR = _supports_color()

def _style(text: str, *codes: str) -> str:
    if not _COLOR or not codes:
        return text
    return "".join(codes) + text + Ansi.RESET

def _hr(char: str = "-", width: int = 45) -> str:
    return char * width

def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())

def _clear_screen() -> None:
    """清屏：使用 ANSI 转义序列清除整个屏幕。"""
    if os.environ.get("TERM") or os.name != "nt":
        # ANSI: 清除整个屏幕(2J) + 移动光标到顶部(H)
        # 注意顺序：先清屏再移动光标
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    else:
        cmd = "cls" if os.name == "nt" else "clear"
        try:
            os.system(cmd)
        except Exception:
            pass

def _configure_text_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass

UI_LANG = "en"  # 'en' (CLI) or 'zh' (TUI)

def _t(en: str, zh: str) -> str:
    return zh if UI_LANG == "zh" else en

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)

def _display_width(text: str) -> int:
    text = _strip_ansi(text)
    width = 0
    for ch in text:
        if ch == "\t":
            width += 4 - (width % 4)
            continue
        if ch in ("\n", "\r"):
            continue
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width

def _pad_right(text: str, width: int) -> str:
    padding = width - _display_width(text)
    if padding <= 0:
        return text
    return text + (" " * padding)

def _take_prefix_by_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""

    out = []
    width = 0
    had_ansi = False

    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            m = _ANSI_RE.match(text, i)
            if m:
                out.append(m.group(0))
                had_ansi = True
                i = m.end()
                continue

        ch = text[i]
        ch_w = _display_width(ch)
        if width + ch_w > max_width:
            break
        out.append(ch)
        width += ch_w
        i += 1

    result = "".join(out)
    if had_ansi and _COLOR and not result.endswith(Ansi.RESET):
        result += Ansi.RESET
    return result

def _take_suffix_by_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""

    tokens = []
    last = 0
    for m in _ANSI_RE.finditer(text):
        if m.start() > last:
            tokens.append(("text", text[last:m.start()]))
        tokens.append(("ansi", m.group(0)))
        last = m.end()
    if last < len(text):
        tokens.append(("text", text[last:]))

    out_rev = []
    width = 0
    had_ansi = False

    for kind, chunk in reversed(tokens):
        if kind == "ansi":
            out_rev.append(chunk)
            had_ansi = True
            continue

        for ch in reversed(chunk):
            ch_w = _display_width(ch)
            if width + ch_w > max_width:
                result = "".join(reversed(out_rev))
                if had_ansi and _COLOR and not result.endswith(Ansi.RESET):
                    result += Ansi.RESET
                return result
            out_rev.append(ch)
            width += ch_w

    result = "".join(reversed(out_rev))
    if had_ansi and _COLOR and not result.endswith(Ansi.RESET):
        result += Ansi.RESET
    return result

def _ellipsize_middle(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _display_width(text) <= max_width:
        return text

    ellipsis = _glyphs().get("ellipsis", "...")
    if max_width <= _display_width(ellipsis) + 1:
        return _take_prefix_by_width(text, max_width)

    prefix_w = (max_width - _display_width(ellipsis)) // 2
    suffix_w = max_width - _display_width(ellipsis) - prefix_w
    return _take_prefix_by_width(text, prefix_w) + ellipsis + _take_suffix_by_width(text, suffix_w)

def _term_width(fallback: int = 90) -> int:
    """
    Get current terminal width in columns.

    Prefer a live TTY ioctl (respects terminal resize) over environment variables
    like COLUMNS/LINES, which can be stale in some shells/launchers.
    """
    try:
        if getattr(sys.stdout, "isatty", lambda: False)():
            try:
                return os.get_terminal_size(sys.stdout.fileno()).columns
            except Exception:
                pass
        return shutil.get_terminal_size(fallback=(fallback, 24)).columns
    except Exception:
        return fallback

def _tui_width(cols: Optional[int] = None, *, fallback: int = 90) -> int:
    """
    Pick a safe, responsive width for TUI layout.

    Notes:
    - Many terminals auto-wrap when a line exactly reaches the last column.
      We keep a small margin by default.
    - Users can cap the width via CSC_TUI_MAX_WIDTH (useful on very wide terminals).
    """
    cols = _term_width(fallback=fallback) if cols is None else int(cols)
    if cols <= 0:
        cols = fallback

    width = cols
    if cols >= 24:
        width = max(24, cols - 2)

    cap = os.environ.get("CSC_TUI_MAX_WIDTH")
    if cap:
        try:
            cap_n = int(cap)
            if cap_n > 0:
                width = min(width, max(24, cap_n))
        except Exception:
            pass

    return max(20, width)

_UNICODE_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}
_ASCII_BOX = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}

_UNICODE_GLYPHS = {
    "pointer": "›",
    "ellipsis": "…",
}
_ASCII_GLYPHS = {
    "pointer": ">",
    "ellipsis": "...",
}

def _glyphs() -> dict:
    if os.environ.get("CSC_ASCII_UI"):
        return _ASCII_GLYPHS
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        ("".join(_UNICODE_GLYPHS.values())).encode(encoding)
        return _UNICODE_GLYPHS
    except Exception:
        return _ASCII_GLYPHS


# Classic Block-Style ASCII Art Font (直接硬编码，避免压缩逻辑错误)
# 使用经典的 Figlet "Banner" 风格，每个字符 5 行高
_LOGO_FONT_BANNER = {
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

# 字体别名（用于适应不同终端宽度的选择逻辑）
_LOGO_FONT_4X5 = _LOGO_FONT_BANNER
_LOGO_FONT_4X7 = _LOGO_FONT_BANNER
_LOGO_FONT_3X7 = _LOGO_FONT_BANNER


def _can_encode(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except Exception:
        return False

def _render_big_logo_text(
    text: str,
    fill: str,
    *,
    char_gap: int = 1,
    word_gap: int = 1,
) -> List[str]:
    rows = [""] * 5
    for ch in text:
        if ch == " ":
            for i in range(5):
                rows[i] += " " * word_gap
            continue

        pattern = _LOGO_FONT_4X5.get(ch.upper())
        if pattern is None:
            pattern = [f"{ch}   " if i == 2 else "    " for i in range(5)]

        for i in range(5):
            rows[i] += pattern[i] + (" " * char_gap)

    return [row.replace("X", fill).rstrip() for row in rows]

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
    fallback_width = max((len(row) for pat in patterns for row in pat), default=4)
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

    # Shadow should add a crisp "depth" effect without filling interior spaces.
    # Only paint shadow that falls outside the base glyph bounding box.
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

def _apply_gradient_to_lines(lines: List[str], colors: Tuple[str, ...]) -> List[str]:
    """Applies a vertical gradient to a list of lines."""
    if not _COLOR or not colors or not lines:
        return lines

    if len(colors) == 1:
        return [_style(line, colors[0]) for line in lines]

    # Simple vertical gradient for now
    steps = len(lines)
    if steps <= 1:
        return [_style(line, colors[0]) for line in lines]

    out = []
    # If we have 2 colors, interpolate. For simplicity in ANSI 16-color mode,
    # we just split the lines into chunks.
    # Future enhancement: TrueColor interpolation.
    
    # Chunk-based gradient
    chunk_size = max(1, steps // len(colors))
    for i, line in enumerate(lines):
        color_idx = min(len(colors) - 1, i // chunk_size)
        out.append(_style(line, colors[color_idx]))
        
    return out

def _style_logo_chars(
    lines: List[str],
    *,
    fill: str,
    shadow: str,
    fill_codes: Tuple[str, ...] = (Ansi.BOLD, Ansi.BRIGHT_CYAN),
    shadow_codes: Tuple[str, ...] = (Ansi.DIM, Ansi.BRIGHT_BLUE),
) -> List[str]:
    if not _COLOR:
        return lines

    shadow_token = _style(shadow, *shadow_codes) if shadow and shadow != " " and shadow_codes else None
    
    # Prepare gradient fill if fill_codes has multiple colors and no other style codes
    # For simplicity, we just use the first color for fill if not doing fancy gradient
    fill_token = _style(fill, *fill_codes) if fill_codes else fill

    out: List[str] = []
    for i, line in enumerate(lines):
        # We can implement a per-line gradient here if we want vertical gradient
        # For now, stick to the simple replacement but allow for future expansion
        
        # If we passed multiple colors in fill_codes that look like colors, pick one based on line index?
        # Let's keep it simple: reliable replacement.
        processed = line
        if shadow_token:
            processed = processed.replace(shadow, shadow_token)
        processed = processed.replace(fill, fill_token)
        out.append(processed)
    return out

def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def _rgb_to_ansi(r: int, g: int, b: int) -> str:
    # ESC[38;2;r;g;bm
    return f"\033[38;2;{r};{g};{b}m"

def _gradient_text(text: str, start_hex: str, end_hex: str) -> str:
    """Apply horizontal gradient to a single line of text."""
    if not _COLOR:
        return text
    
    # Strip existing ansi to be safe
    clean_text = _strip_ansi(text)
    length = len(clean_text)
    if length <= 1:
        return _style(text, _rgb_to_ansi(*_hex_to_rgb(start_hex)))

    r1, g1, b1 = _hex_to_rgb(start_hex)
    r2, g2, b2 = _hex_to_rgb(end_hex)
    
    out = []
    for i, char in enumerate(clean_text):
        if char == " ":
            out.append(char)
            continue
            
        t = i / (length - 1)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        
        out.append(f"{_rgb_to_ansi(r, g, b)}{char}")
        
    out.append(Ansi.RESET)
    return "".join(out)


def _app_logo_lines(max_width: Optional[int] = None) -> List[str]:
    max_width = _term_width() if max_width is None else max(20, int(max_width))

    ascii_ui = bool(os.environ.get("CSC_ASCII_UI"))
    if not ascii_ui and not _can_encode("█"):
        ascii_ui = True

    fill = "#" if ascii_ui else "█"
    shadow = "." if ascii_ui else ("░" if _can_encode("░") else " ")

    def _normalize_logo_block(lines: List[str]) -> List[str]:
        if not lines:
            return lines
        block_width = max((_display_width(line) for line in lines), default=0)
        return [_pad_right(line, block_width) for line in lines]

    def _render_wordmark(
        text: str,
        *,
        font: dict,
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
        base_width = max((_display_width(line) for line in base), default=0)
        shadow_char = shadow if shadow_ok else " "
        extend_width = shadow_char != " " and (base_width + 1 <= max_width)
        with_shadow = _apply_logo_shadow(
            base,
            fill=fill,
            shadow=shadow_char,
            extend_width=extend_width,
            extend_height=(shadow_char != " "),
        )
        
        # Split fill and shadow rendering
        # If gradient is present, we need to apply it to the FILL characters only, line by line
        if gradient and _COLOR:
            out = []
            start_hex, end_hex = gradient
            
            # Pre-calc shadow string if needed
            shadow_token = _style(shadow_char, *shadow_codes) if shadow_char != " " else " "
            
            for line in with_shadow:
                # This is tricky because we need to gradient ONLY the fill characters, preserving their position
                # But _gradient_text applies to a string.
                # Simplified approach: Render the whole line as gradient? No, that colors spaces too.
                # Better approach: Construct the line manually.
                
                # 1. Identify fill indices
                fill_indices = [i for i, ch in enumerate(line) if ch == fill]
                if not fill_indices:
                    out.append(line.replace(shadow_char, shadow_token))
                    continue
                    
                # 2. Generate gradient colors for the fill range
                # We map the horizontal position of the char to the gradient
                line_len = len(line)
                
                r1, g1, b1 = _hex_to_rgb(start_hex)
                r2, g2, b2 = _hex_to_rgb(end_hex)
                
                new_line_chars = []
                for i, char in enumerate(line):
                    if char == fill:
                        # Gradient color based on X position
                        t = i / max(1, line_len - 1)
                        r = int(r1 + (r2 - r1) * t)
                        g = int(g1 + (g2 - g1) * t)
                        b = int(b1 + (b2 - b1) * t)
                        new_line_chars.append(f"\033[38;2;{r};{g};{b}m{fill}\033[0m")
                    elif char == shadow_char:
                        new_line_chars.append(shadow_token)
                    else:
                        new_line_chars.append(char)
                out.append("".join(new_line_chars))
            return out
        else:
            return _style_logo_chars(
                with_shadow,
                fill=fill,
                shadow=shadow_char,
                fill_codes=fill_codes,
                shadow_codes=shadow_codes,
            )

    def _max_w(lines: List[str]) -> int:
        return max((_display_width(line) for line in lines), default=0)

    def _pad_height(lines: List[str], height: int) -> List[str]:
        return lines + ([""] * max(0, height - len(lines)))

    def _merge_horiz(left: List[str], right: List[str], *, gap: int) -> List[str]:
        left = _normalize_logo_block(left)
        right = _normalize_logo_block(right)
        lw = _max_w(left)
        rw = _max_w(right)
        height = max(len(left), len(right))
        left = left + [_pad_right("", lw)] * (height - len(left))
        right = right + [_pad_right("", rw)] * (height - len(right))
        spacer = " " * max(0, int(gap))
        return [l + spacer + r for l, r in zip(left, right)]

    def _ideal_part_gap(*, min_gap: int) -> int:
        # Keep words readable on narrow terminals, and scale gently on wide ones.
        return max(min_gap, min(18, max_width // 20))

    def _render_parts(font: dict, *, char_gap: int) -> Tuple[List[str], List[str], List[str]]:
        return (
            _render_wordmark(
                "CODEX",
                font=font,
                char_gap=char_gap,
                word_gap=0,
                shadow_ok=True, # Enable shadow for main logo
                fill_codes=(), # Handled by gradient
                shadow_codes=(Ansi.DIM, Ansi.BLUE), # Simple blue shadow
                gradient=("#00FFFF", "#0088FF"), # Cyan -> Blue
            ),
            _render_wordmark(
                "SESSION",
                font=font,
                char_gap=char_gap,
                word_gap=0,
                shadow_ok=True,
                fill_codes=(),
                shadow_codes=(Ansi.DIM, Ansi.MAGENTA),
                gradient=("#FF00FF", "#8800FF"), # Magenta -> Purple
            ),
            _render_wordmark(
                "CLONER",
                font=font,
                char_gap=char_gap,
                word_gap=0,
                shadow_ok=True,
                fill_codes=(),
                shadow_codes=(Ansi.DIM, Ansi.BLUE),
                gradient=("#0088FF", "#0000FF"), # Blue -> Dark Blue
            ),
        )


    def _try_triple_line(font: dict, *, char_gap: int, min_gap: int) -> Optional[List[str]]:
        codex, session, cloner = _render_parts(font, char_gap=char_gap)
        base_sum = _max_w(codex) + _max_w(session) + _max_w(cloner)
        max_gap = (max_width - base_sum) // 2
        if max_gap < min_gap:
            return None
        part_gap = min(max_gap, _ideal_part_gap(min_gap=min_gap))
        line = _merge_horiz(_merge_horiz(codex, session, gap=part_gap), cloner, gap=part_gap)
        if _max_w(line) <= max_width:
            return _normalize_logo_block(line)
        return None

    def _try_stacked(font: dict, *, char_gap: int, min_gap: int) -> Optional[List[str]]:
        codex, session, cloner = _render_parts(font, char_gap=char_gap)

        # 2-line stack: CODEX / SESSION CLONER
        bottom_base = _max_w(session) + _max_w(cloner)
        bottom_max_gap = max_width - bottom_base
        if bottom_max_gap >= min_gap and _max_w(codex) <= max_width:
            bottom_gap = min(bottom_max_gap, _ideal_part_gap(min_gap=min_gap))
            bottom = _merge_horiz(session, cloner, gap=bottom_gap)
            stacked = _normalize_logo_block(codex) + _normalize_logo_block(bottom)
            if _max_w(stacked) <= max_width:
                return stacked

        # 3-line stack: CODEX / SESSION / CLONER
        if _max_w(codex) <= max_width and _max_w(session) <= max_width and _max_w(cloner) <= max_width:
            stacked = _normalize_logo_block(codex) + _normalize_logo_block(session) + _normalize_logo_block(cloner)
            if _max_w(stacked) <= max_width:
                return stacked

        return None

    # Prefer readability first (letter spacing), then detail (font size).
    # Keep at least 2 spaces between words; if we can't, switch to a tighter font/gaps
    # instead of squeezing words together.
    for font, char_gap in (
        (_LOGO_FONT_4X7, 1),
        (_LOGO_FONT_4X5, 1),
        (_LOGO_FONT_3X7, 1),
        (_LOGO_FONT_4X7, 0),
        (_LOGO_FONT_4X5, 0),
        (_LOGO_FONT_3X7, 0),
    ):
        candidate = _try_triple_line(font, char_gap=char_gap, min_gap=2)
        if candidate:
            return candidate

    # Very narrow terminals: keep the wordmark by stacking lines before falling back.
    for font in (_LOGO_FONT_4X5, _LOGO_FONT_3X7):
        for char_gap in (1, 0):
            candidate = _try_stacked(font, char_gap=char_gap, min_gap=2)
            if candidate:
                return candidate

    # Last-resort: full product name in compact 3x7 (single block).
    full_text = "CODEX SESSION CLONER"
    for spec in ({"char_gap": 0, "word_gap": 1}, {"char_gap": 0, "word_gap": 0}):
        full = _render_wordmark(
            full_text,
            font=_LOGO_FONT_3X7,
            fill_codes=(Ansi.BOLD, Ansi.BRIGHT_CYAN),
            shadow_codes=(),
            shadow_ok=False,
            **spec,
        )
        if _max_w(full) <= max_width:
            return _normalize_logo_block(full)

    # 5) Too narrow: keep an ASCII-art presence with acronym + short name.
    acronym = _render_wordmark(
        "CSC",
        font=_LOGO_FONT_3X7,
        char_gap=1,
        word_gap=2,
        shadow_ok=True,
        gradient=("#00FFFF", "#0000FF"),
    )
    short = "codex-session-cloner"
    segments = short.split("-")
    if _COLOR and len(segments) == 3:
        seg_colors = (Ansi.BRIGHT_CYAN, Ansi.BRIGHT_MAGENTA, Ansi.BRIGHT_BLUE)
        dash = _style("-", Ansi.DIM)
        short_line = dash.join(_style(seg, Ansi.BOLD, color) for seg, color in zip(segments, seg_colors))
    else:
        short_line = short
    return _normalize_logo_block(acronym) + [_ellipsize_middle(short_line, max_width)]

def _gemini_logo_lines() -> List[str]:
    # Backwards-compatible alias for the header wordmark.
    return _app_logo_lines(max_width=_tui_width())

def _align_line(line: str, width: int, *, center: bool) -> str:
    if not center:
        return line
    padding = (max(0, width - _display_width(line))) // 2
    return (" " * padding) + line

def _box_chars() -> dict:
    if os.environ.get("CSC_ASCII_UI"):
        return _ASCII_BOX
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        ("".join(_UNICODE_BOX.values())).encode(encoding)
        return _UNICODE_BOX
    except Exception:
        return _ASCII_BOX

def _render_box(lines, width=None, border_codes=None) -> List[str]:
    cols = _term_width()
    if width is None:
        width = min(cols, 90)
    width = min(cols, max(24, int(width)))
    inner = max(1, width - 4)

    box = _box_chars()
    top = box["tl"] + (box["h"] * (width - 2)) + box["tr"]
    bottom = box["bl"] + (box["h"] * (width - 2)) + box["br"]

    out = [_style(top, *(border_codes or ()))]
    for line in lines:
        text = _pad_right(_ellipsize_middle(str(line), inner), inner)
        if border_codes:
            left = _style(box["v"], *border_codes)
            right = _style(box["v"], *border_codes)
            row = f"{left} {text} {right}"
        else:
            row = f"{box['v']} {text} {box['v']}"

        if _COLOR:
            row += Ansi.RESET
        out.append(row)

    bottom_line = _style(bottom, *(border_codes or ()))
    if _COLOR:
        bottom_line += Ansi.RESET
    out.append(bottom_line)
    return out

def _read_key() -> str:
    if os.name == "nt":
        try:
            import msvcrt
        except Exception:
            return None

        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
            }.get(ch2)
        return ch

    try:
        import select
        import termios
        import tty
    except Exception:
        return None

    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception:
        return None

    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if not ch:
            return None
        if ch in (b"\r", b"\n"):
            return "ENTER"
        if ch == b"\x03":
            raise KeyboardInterrupt
        if ch == b"\x1b":
            if select.select([fd], [], [], 0.05)[0]:
                ch2 = os.read(fd, 1)
                if ch2 in (b"[", b"O") and select.select([fd], [], [], 0.05)[0]:
                    ch3 = os.read(fd, 1)
                    return {
                        b"A": "UP",
                        b"B": "DOWN",
                        b"C": "RIGHT",
                        b"D": "LEFT",
                    }.get(ch3, "ESC")
                return "ESC"
            return "ESC"
        try:
            return ch.decode("utf-8")
        except Exception:
            return chr(ch[0])
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass

def strip_toml_comment(line: str) -> str:
    in_single_quote = False
    in_double_quote = False
    escaped = False
    out = []

    for ch in line:
        if in_double_quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_double_quote = False
        elif in_single_quote:
            if ch == "'":
                in_single_quote = False
        else:
            if ch == "#":
                break
            if ch == '"':
                in_double_quote = True
            elif ch == "'":
                in_single_quote = True

        out.append(ch)

    return "".join(out)

def parse_toml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return None

    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        if value[0] == "'":
            return value[1:-1]
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, str):
                return parsed
        except Exception:
            return value[1:-1]

    return value.split()[0]

def get_current_provider() -> str:
    """Find 'model_provider' in config.toml (robust to whitespace/comments)."""
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_PROVIDER
        
    try:
        if tomllib is not None:
            try:
                with open(CONFIG_FILE, "rb") as f:
                    data = tomllib.load(f)
                provider = data.get("model_provider")
                if isinstance(provider, str) and provider.strip():
                    return provider.strip()
            except Exception:
                pass

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = strip_toml_comment(raw_line).strip()
                if not line:
                    continue
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                if key.strip() != "model_provider":
                    continue

                provider = parse_toml_scalar(value)
                if isinstance(provider, str) and provider.strip():
                    return provider.strip()
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        
    return DEFAULT_PROVIDER

TARGET_PROVIDER = get_current_provider()

def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clone Codex sessions to current provider.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files")
    parser.add_argument("--clean", action="store_true", help="Remove unmarked clones from previous runs")
    parser.add_argument("--no-tui", action="store_true", help="Force CLI mode even in interactive terminal")
    return parser

def print_header(dry_run: bool) -> None:
    title = _style("Codex Session Cloner", Ansi.BOLD, Ansi.CYAN)
    print(_hr("="))
    print(title)
    print(_hr("="))
    print(f"OS:            {platform.system()} ({os.name})")
    print(f"Python:        {sys.version.split()[0]}")
    print(f"TargetProvider:{TARGET_PROVIDER}")
    print(f"SessionsDir:   {SESSIONS_DIR}")
    print(f"ConfigFile:    {CONFIG_FILE}")
    if dry_run:
        print(_style("DRY-RUN MODE (no write / no delete)", Ansi.BOLD, Ansi.YELLOW))
    print(_hr())

def scan_existing_clones(sessions_dir, target_provider):
    """
    Pass 1: Scan ALL files to build an index of already cloned sessions.
    Returns a set of original UUIDs that have already been cloned.
    """
    cloned_from_ids = set()
    total_files = 0
    
    print(_t("Building Clone Index...", "构建克隆索引..."), end="", flush=True)
    
    for root, dirs, files in os.walk(sessions_dir):
        for file in files:
            if not file.endswith(".jsonl"):
                continue
                
            total_files += 1
            full_path = os.path.join(root, file)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    line = f.readline()
                    if not line: continue
                    meta = json.loads(line)
                    payload = meta.get('payload', {})
                    
                    # If this is a session belonging to our target provider...
                    if payload.get('model_provider') == target_provider:
                        # ...check if it claims to be a clone
                        origin_id = payload.get('cloned_from')
                        if origin_id:
                            cloned_from_ids.add(origin_id)
            except Exception:
                continue
                
    if UI_LANG == "zh":
        print(f" 完成。共扫描 {total_files} 个文件，找到 {len(cloned_from_ids)} 个已克隆会话。")
    else:
        print(f" Done. Found {len(cloned_from_ids)} existing clones out of {total_files} files.")
    return cloned_from_ids

def clone_session(file_path, already_cloned_ids, dry_run=False):
    """
    Reads a session file, clones it if appropriate.
    Returns: (Action, Message)
      Action: 'cloned', 'skipped_exists', 'skipped_provider', 'error', 'skipped_target'
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        if not lines:
            return 'error', _t("Empty file", "空文件")

        # Parse metadata
        try:
            meta = json.loads(lines[0])
        except json.JSONDecodeError:
            return 'error', _t("Invalid JSON", "JSON 无效")

        if meta.get('type') != 'session_meta':
            return 'error', _t("Not a session file", "不是 Codex session 文件")
            
        payload = meta.get('payload', {})
        current_provider = payload.get('model_provider')
        current_id = payload.get('id')
        
        # 1. Skip if it IS the target provider (we don't clone ourselves)
        if current_provider == TARGET_PROVIDER:
            return 'skipped_target', _t("Already on target provider", "已是目标 provider")
            
        # 2. Skip if already cloned
        if current_id in already_cloned_ids:
            return 'skipped_exists', _t(
                f"Already cloned (ID: {current_id})",
                f"已克隆过（ID: {current_id}）",
            )

        # --- Prepare Clone ---
        
        new_id = str(uuid.uuid4())
        
        # Source Marking (use copy to allow repeated dry-runs safely/pure function)
        new_payload = payload.copy()
        new_payload['id'] = new_id
        new_payload['model_provider'] = TARGET_PROVIDER
        new_payload['cloned_from'] = current_id
        new_payload['original_provider'] = current_provider
        new_payload['clone_timestamp'] = datetime.now().isoformat()
        
        # Update metadata line
        meta['payload'] = new_payload
        lines[0] = json.dumps(meta) + "\n"
        
        # Construct new filename
        file_path_obj = Path(file_path)
        old_filename = file_path_obj.name
        
        # New filename logic
        if current_id and current_id in old_filename:
            new_filename = old_filename.replace(current_id, new_id)
        else:
            if old_filename.endswith(f"{current_id}.jsonl"):
                new_filename = old_filename.replace(f"{current_id}.jsonl", f"{new_id}.jsonl")
            else:
                 new_filename = f"rollout-CLONE-{new_id}.jsonl"

        new_file_path = file_path_obj.parent / new_filename
        
        if new_file_path.exists():
            return 'skipped_exists', _t("Target file collision", "目标文件冲突（同名）")

        if not dry_run:
            with open(new_file_path, 'w', encoding='utf-8') as f_out:
                f_out.writelines(lines)
            return 'cloned', _t(
                f"Created {new_filename} (from {current_provider})",
                f"已创建 {new_filename}（来自 {current_provider}）",
            )
        else:
            return 'cloned', _t(
                f"[DRY-RUN] Would create {new_filename} (from {current_provider})",
                f"[DRY-RUN] 将创建 {new_filename}（来自 {current_provider}）",
            )

    except Exception as e:
        return 'error', str(e)

def scan_for_cleanup(sessions_dir, target_provider, dry_run=False):
    """
    Scans for 'orphan' clones in O(N).
    Single pass to collect all relevant file info, then filter in memory.
    """
    print(_t("Scanning for unmarked clones to clean up...", "扫描待清理的旧版无标记副本..."))
    
    # In-memory stores
    # Key: extracted_timestamp_string, Value: List of file_paths
    originals_by_ts = {}
    targets_without_tag_by_ts = {}
    
    files_checked = 0
    
    # 1. Single Pass Scan
    for root, dirs, files in os.walk(sessions_dir):
        for file in files:
            if not file.endswith(".jsonl"): continue
            
            files_checked += 1
            full_path = os.path.join(root, file)
            # Optimization: Try to get timestamp from filename first without opening
            ts = extract_timestamp_from_filename(file)
            if not ts: continue
            
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    line = f.readline()
                    if not line: continue
                    meta = json.loads(line)
                    payload = meta.get('payload', {})
                    provider = payload.get('model_provider')
                    
                    if provider == target_provider:
                        # Potential orphan clone?
                        if 'cloned_from' not in payload:
                            if ts not in targets_without_tag_by_ts:
                                targets_without_tag_by_ts[ts] = []
                            targets_without_tag_by_ts[ts].append(full_path)
                    else:
                        # This is an original source
                        originals_by_ts[ts] = True
            except Exception:
                continue

    # 2. Correlate
    files_to_delete = []
    
    for ts, paths in targets_without_tag_by_ts.items():
        # If we have an original with this timestamp...
        if ts in originals_by_ts:
            # ...then these unmarked targets are indeed orphans of that original
            files_to_delete.extend(paths)
                
    # 3. Execute Cleanup
    if UI_LANG == "zh":
        print(f"共扫描 {files_checked} 个文件，找到 {len(files_to_delete)} 个可清理目标。")
    else:
        print(f"Scanned {files_checked} files. Found {len(files_to_delete)} unmarked clones.")
    
    for fpath in files_to_delete:
        if dry_run:
            print(_t(f"[DRY-RUN] Would delete: {fpath}", f"[DRY-RUN] 将删除：{fpath}"))
        else:
            try:
                os.remove(fpath)
                print(_t(f"[Deleted] {fpath}", f"[已删除] {fpath}"))
            except Exception as e:
                print(_t(f"[Error] Deleting {fpath}: {e}", f"[错误] 删除失败 {fpath}: {e}"))

def extract_timestamp_from_filename(filename):
    # rollout-2025-10-10T14-53-44-442631c4... .jsonl
    # We want "2025-10-10T14-53-44"
    try:
        if not filename.startswith("rollout-"): return None
        # Remove prefix
        rest = filename[8:]
        # Remove suffix
        if rest.endswith(".jsonl"): rest = rest[:-6]
        
        parts = rest.split('-')
        if len(parts) > 5:
            if (len(parts[-1]) == 12 and len(parts[-2]) == 4 and 
                len(parts[-3]) == 4 and len(parts[-4]) == 4 and len(parts[-5]) == 8):
                return "-".join(parts[:-5])
        return None
    except Exception:
        return None

def run_clone(dry_run: bool) -> int:
    # Pass 1: Index
    already_cloned = scan_existing_clones(SESSIONS_DIR, TARGET_PROVIDER)
    
    # Pass 2: Clone
    stats = {
        'cloned': 0,
        'skipped_exists': 0,
        'skipped_target': 0,
        'error': 0
        # 'skipped_provider' removed as it was unused
    }
    
    print(_t("\nScanning candidates...", "\n扫描候选会话..."))
    
    for root, dirs, files in os.walk(SESSIONS_DIR):
        for file in files:
            if file.endswith(".jsonl"):
                full_path = os.path.join(root, file)
                action, msg = clone_session(full_path, already_cloned, dry_run=dry_run)
                
                stats[action] = stats.get(action, 0) + 1
                
                if action == 'cloned':
                    print(_style("[+]", Ansi.GREEN), msg)
                elif action == 'error':
                    print(_style("[!]", Ansi.RED), _t(f"Error in {file}: {msg}", f"{file} 出错：{msg}"))
                    
    print("\n" + _hr("=", 30))
    if UI_LANG == "zh":
        print("汇总：")
        print(f"  目标 Provider : {TARGET_PROVIDER}")
        print(f"  新增克隆      : {stats['cloned']}")
        print(f"  跳过（目标）  : {stats['skipped_target']}（已属于 {TARGET_PROVIDER}）")
        print(f"  跳过（已做）  : {stats['skipped_exists']}（已克隆过）")
        print(f"  错误          : {stats['error']}")
    else:
        print("Summary:")
        print(f"  Target Provider: {TARGET_PROVIDER}")
        print(f"  Cloned (New):    {stats['cloned']}")
        print(f"  Skipped (Target):{stats['skipped_target']} (Files already belonging to {TARGET_PROVIDER})")
        print(f"  Skipped (Done):  {stats['skipped_exists']} (Others already cloned previously)")
        print(f"  Errors:          {stats['error']}")
    print(_hr("=", 30))
    
    if dry_run:
        print(_t("\nThis was a DRY RUN. No files were created.", "\n这是 DRY-RUN：未创建任何文件。"))
    return 0

def run_cleanup(dry_run: bool) -> int:
    if not dry_run:
        print(_style(_t("WARNING: --clean will DELETE files.", "警告：--clean 会删除文件。"), Ansi.BOLD, Ansi.YELLOW))
    scan_for_cleanup(SESSIONS_DIR, TARGET_PROVIDER, dry_run=dry_run)
    print(_t("\nCleanup scan complete.", "\n清理完成。"))
    return 0

def run_tui() -> int:
    global UI_LANG
    previous_lang = UI_LANG
    UI_LANG = "zh"

    items = [
        {"id": "clone", "hotkey": "1", "label": "克隆会话（幂等）", "cli_args": ["--no-tui"], "danger": False, "dry_run": False},
        {"id": "clone_dry", "hotkey": "2", "label": "模拟克隆（Dry-run，不写入）", "cli_args": ["--dry-run"], "danger": False, "dry_run": True},
        {"id": "clean", "hotkey": "3", "label": "清理旧版无标记副本（删除）", "cli_args": ["--clean"], "danger": True, "dry_run": False},
        {"id": "clean_dry", "hotkey": "4", "label": "模拟清理（Dry-run，不删除）", "cli_args": ["--clean", "--dry-run"], "danger": True, "dry_run": True},
        {"id": "help", "hotkey": "5", "label": "帮助 / CLI 参数说明", "cli_args": ["--help"], "danger": False, "dry_run": False},
        {"id": "exit", "hotkey": "0", "label": "退出", "cli_args": [], "danger": False, "dry_run": False},
    ]

    def _python_cmd_preview() -> str:
        return "python" if os.name == "nt" else "python3"

    def _cli_preview(args) -> str:
        cmd = f"{_python_cmd_preview()} codex-session-cloner.py"
        if args:
            cmd += " " + " ".join(args)
        return cmd

    def _tui_help_text() -> None:
        cols = _term_width()
        box_width = _tui_width(cols)
        title = _style("帮助 / 使用说明", Ansi.BOLD, Ansi.CYAN)
        print(title)
        print(_style("（在交互终端无参数运行默认进入本菜单）", Ansi.DIM))
        print("")

        lines = [
            _style("常用操作：", Ansi.BOLD),
            "  1) 克隆会话（幂等）：不会重复克隆同一个源会话",
            "  2) Dry-run：预览将要创建/删除的文件，不做实际修改",
            "  3) Clean：清理旧版无标记副本（会删除文件，TUI 需输入 DELETE 二次确认）",
            "",
            _style("CLI 参数（带参数会跳过菜单，直接执行）：", Ansi.BOLD),
            "  --dry-run          模拟运行（不写入/不删除）",
            "  --clean            清理旧版无标记副本（删除）",
            "  --no-tui           即使无参数也不进菜单（直接执行克隆）",
            "",
            _style("示例：", Ansi.BOLD),
            f"  {_python_cmd_preview()} codex-session-cloner.py --dry-run",
            f"  {_python_cmd_preview()} codex-session-cloner.py --clean --dry-run",
            f"  {_python_cmd_preview()} codex-session-cloner.py --no-tui",
            "",
            _style("终端兼容：", Ansi.BOLD),
            "  NO_COLOR=1         关闭颜色输出",
            "  CSC_ASCII_UI=1     强制使用 ASCII 边框（不支持 Unicode 时可用）",
            "  CSC_TUI_MAX_WIDTH= 限制 TUI 最大宽度（用于超宽终端）",
        ]
        for line in _render_box(lines, width=box_width, border_codes=(Ansi.DIM,)):
            print(line)
        print("")
        input("按 Enter 返回菜单...")

    def _render_home(selected_index: int) -> None:
        """渲染主界面（双缓冲：先构建所有行，再一次性输出）"""
        cols = _term_width()
        box_width = _tui_width(cols)
        glyphs = _glyphs()
        pointer = glyphs.get("pointer", ">")

        center = box_width >= 70
        
        # === 双缓冲：收集所有输出行 ===
        output_lines: List[str] = []
        
        for line in _app_logo_lines(max_width=box_width):
            output_lines.append(_align_line(line, box_width, center=center))
        title = _style("Codex 会话克隆器", Ansi.BOLD, Ansi.CYAN)
        subtitle = _style("↑/↓ 选择 · Enter 执行", Ansi.DIM)
        output_lines.append(_align_line(title, box_width, center=center))
        output_lines.append(_align_line(subtitle, box_width, center=center))
        output_lines.append("")

        selected = items[selected_index]
        preview_cmd = _cli_preview(selected["cli_args"])

        info_lines = [
            f"{_style('目标 Provider', Ansi.DIM)} : {_style(TARGET_PROVIDER, Ansi.BOLD, Ansi.CYAN)}",
            f"{_style('会话目录', Ansi.DIM)}      : {_style(SESSIONS_DIR, Ansi.DIM)}",
            f"{_style('配置文件', Ansi.DIM)}      : {_style(CONFIG_FILE, Ansi.DIM)}",
            f"{_style('CLI 等效命令', Ansi.DIM)}  : {_style(preview_cmd, Ansi.BOLD, Ansi.MAGENTA)}",
        ]
        if selected["id"] == "clone":
            info_lines.append(_style("说明", Ansi.DIM) + "           : 无参数默认进入菜单；加 --no-tui 可直接克隆。")
        if selected["id"] == "clean":
            info_lines.append(_style("【危险】", Ansi.BOLD, Ansi.RED) + "该操作会删除文件，且无法恢复。")
            info_lines.append(_style("        ", Ansi.DIM) + "执行前需要输入 DELETE 二次确认。")
        elif selected["id"] == "clean_dry":
            info_lines.append(_style("【提示】", Ansi.BOLD, Ansi.YELLOW) + "这是模拟清理，不会删除文件。")
        elif selected["id"] == "clone_dry":
            info_lines.append(_style("【DRY-RUN】", Ansi.BOLD, Ansi.YELLOW) + "不会创建任何文件。")

        for line in _render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            output_lines.append(line)

        output_lines.append("")

        menu_lines = []
        for idx, item in enumerate(items):
            key = f"[{item['hotkey']}]"
            label = f"{key} {item['label']}"
            if idx == selected_index:
                selected_prefix = _style(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
                if item["id"] == "clean":
                    line = selected_prefix + _style(label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.RED)
                elif item["id"] == "clean_dry":
                    line = selected_prefix + _style(label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.YELLOW)
                elif item["id"] == "help":
                    line = selected_prefix + _style(label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.GREEN)
                elif item["id"] == "exit":
                    line = selected_prefix + _style(label, Ansi.BOLD, Ansi.DIM)
                else:
                    line = selected_prefix + _style(label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.CYAN)
            else:
                line = "  " + _style(key, Ansi.DIM, Ansi.CYAN) + " " + item["label"]
            menu_lines.append(line)

        for line in _render_box(menu_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            output_lines.append(line)

        output_lines.append("")
        if os.name == "nt":
            hint = "提示：双击 快速启动.bat 启动；或运行 ./csc-launcher.ps1"
        else:
            hint = "提示：运行 ./csc-launcher.sh 启动（可追加参数，如：--dry-run）"
        output_lines.append(_style(hint, Ansi.DIM))
        output_lines.append(
            _style("按键：", Ansi.DIM)
            + _style("↑/↓", Ansi.BOLD, Ansi.CYAN)
            + _style(" 选择", Ansi.DIM)
            + _style(" · ", Ansi.DIM)
            + _style("Enter", Ansi.BOLD, Ansi.GREEN)
            + _style(" 执行", Ansi.DIM)
            + _style(" · ", Ansi.DIM)
            + _style("1-5", Ansi.BOLD, Ansi.CYAN)
            + _style(" 快捷", Ansi.DIM)
            + _style(" · ", Ansi.DIM)
            + _style("h", Ansi.BOLD, Ansi.CYAN)
            + _style(" 帮助", Ansi.DIM)
            + _style(" · ", Ansi.DIM)
            + _style("q", Ansi.BOLD, Ansi.CYAN)
            + _style(" 退出", Ansi.DIM)
        )
        
        # === 隐藏光标 + 一次性输出 + 显示光标 ===
        hide_cursor = "\033[?25l"
        show_cursor = "\033[?25h"
        home_cursor = "\033[H"
        clear_to_eol = "\033[K"  # 清除到行尾
        clear_to_eos = "\033[J"  # 清除到屏幕底部
        
        # 每行末尾添加清除到行尾，防止残留
        full_output = "\n".join(line + clear_to_eol for line in output_lines) + "\n"
        
        # 一次性写入：隐藏光标 -> 光标归位 -> 输出内容 -> 清除剩余 -> 显示光标
        sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
        sys.stdout.flush()


    def _run_action(action_name: str, cli_args, dry_run: bool, runner, danger: bool) -> None:
        cols = _term_width()
        box_width = _tui_width(cols)
        _clear_screen()

        center = box_width >= 70
        for line in _app_logo_lines(max_width=box_width):
            print(_align_line(line, box_width, center=center))
        title = _style("Codex 会话克隆器", Ansi.BOLD, Ansi.CYAN)
        subtitle = _style("执行中…", Ansi.DIM)
        print(_align_line(title, box_width, center=center))
        print(_align_line(subtitle, box_width, center=center))

        color = Ansi.CYAN
        if danger and not dry_run:
            color = Ansi.RED
        elif dry_run:
            color = Ansi.YELLOW

        print(_style(f"▶ {action_name}", Ansi.BOLD, color))
        print("")

        info_lines = [
            f"{_style('CLI 等效命令', Ansi.DIM)}  : {_style(_cli_preview(cli_args), Ansi.BOLD, Ansi.MAGENTA)}",
            f"{_style('目标 Provider', Ansi.DIM)} : {_style(TARGET_PROVIDER, Ansi.BOLD, Ansi.CYAN)}",
            f"{_style('会话目录', Ansi.DIM)}      : {_style(SESSIONS_DIR, Ansi.DIM)}",
        ]
        if danger and not dry_run:
            info_lines.append(_style("【危险】", Ansi.BOLD, Ansi.RED) + "将删除文件，无法恢复。")
        elif dry_run:
            info_lines.append(_style("【DRY-RUN】", Ansi.BOLD, Ansi.YELLOW) + "不写入/不删除。")

        for line in _render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        runner()
        input(_style("\n按 Enter 返回菜单...", Ansi.DIM))

    def _confirm_dangerous_action(cli_args) -> bool:
        _clear_screen()
        cols = _term_width()
        box_width = _tui_width(cols)

        center = box_width >= 70
        for line in _app_logo_lines(max_width=box_width):
            print(_align_line(line, box_width, center=center))
        title = _style("危险操作确认", Ansi.BOLD, Ansi.RED)
        subtitle = _style("该操作会删除文件，且无法恢复。", Ansi.DIM)
        print(_align_line(title, box_width, center=center))
        print(_align_line(subtitle, box_width, center=center))
        print("")

        info_lines = [
            _style("【危险】", Ansi.BOLD, Ansi.RED) + "Clean 会删除旧版无标记副本文件。",
            f"{_style('CLI 等效命令', Ansi.DIM)} : {_style(_cli_preview(cli_args), Ansi.BOLD, Ansi.MAGENTA)}",
            "",
            "确认方式：输入 DELETE 并回车。",
            "取消方式：直接回车。",
        ]
        for line in _render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.RED)):
            print(line)
        print("")

        confirm = input(_style("请输入 DELETE 确认执行：", Ansi.BOLD, Ansi.RED)).strip()
        return confirm == "DELETE"

    try:
        selected = 0
        hotkey_to_index = {item["hotkey"]: idx for idx, item in enumerate(items)}
        
        # 只在首次进入时清屏，之后 _render_home 用光标归位实现无闪烁刷新
        _clear_screen()

        while True:
            _render_home(selected)

            key = _read_key()
            if key is None:
                choice = input("请选择 [1]：").strip().lower()
                if not choice:
                    choice = "1"
                key = choice

            if key == "UP" or key in ("k", "K"):
                selected = (selected - 1) % len(items)
                continue
            if key == "DOWN" or key in ("j", "J"):
                selected = (selected + 1) % len(items)
                continue

            if key == "ENTER":
                choice_id = items[selected]["id"]
            else:
                key_str = str(key).strip().lower()
                if key_str in {"q", "quit", "exit", "0"}:
                    return 0
                if key_str in {"h", "help", "?"}:
                    _clear_screen()
                    _tui_help_text()
                    continue

                if key_str in hotkey_to_index:
                    selected = hotkey_to_index[key_str]
                    choice_id = items[selected]["id"]
                else:
                    continue

            chosen = items[selected]

            if choice_id == "exit":
                return 0
            if choice_id == "help":
                _clear_screen()
                _tui_help_text()
                continue
            if choice_id == "clone":
                _run_action("克隆会话（幂等）", chosen["cli_args"], dry_run=False, runner=lambda: run_clone(dry_run=False), danger=False)
                continue
            if choice_id == "clone_dry":
                _run_action("模拟克隆（Dry-run）", chosen["cli_args"], dry_run=True, runner=lambda: run_clone(dry_run=True), danger=False)
                continue
            if choice_id == "clean":
                if not _confirm_dangerous_action(chosen["cli_args"]):
                    continue
                _run_action("清理旧版无标记副本（删除）", chosen["cli_args"], dry_run=False, runner=lambda: run_cleanup(dry_run=False), danger=True)
                continue
            if choice_id == "clean_dry":
                _run_action("模拟清理（Dry-run）", chosen["cli_args"], dry_run=True, runner=lambda: run_cleanup(dry_run=True), danger=True)
                continue
    finally:
        UI_LANG = previous_lang

def main(argv=None) -> int:
    _configure_text_streams()
    parser = create_arg_parser()
    if argv is None:
        argv = sys.argv[1:]

    # Default: no args -> friendly TUI (only when interactive).
    if not argv and _is_interactive():
        try:
            return run_tui()
        except KeyboardInterrupt:
            return 130

    args = parser.parse_args(argv)
    print_header(dry_run=bool(args.dry_run))

    if args.clean:
        return run_cleanup(dry_run=bool(args.dry_run))
    return run_clone(dry_run=bool(args.dry_run))

if __name__ == "__main__":
    raise SystemExit(main())
