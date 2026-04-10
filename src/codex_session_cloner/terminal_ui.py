"""
Terminal UI primitives for the Codex Session Toolkit.

This module keeps rendering, layout, styling, and keyboard helpers separate
from the application workflow so the CLI/TUI shell can focus on behavior.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
import unicodedata
from typing import List, Optional, Tuple


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


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term.lower() == "dumb":
        return False
    return True


COLOR_ENABLED = supports_color()
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

UNICODE_BOX_CHARS = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}
ASCII_BOX_CHARS = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}

UNICODE_GLYPHS = {
    "pointer": "›",
    "ellipsis": "…",
}
ASCII_GLYPHS = {
    "pointer": ">",
    "ellipsis": "...",
}

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

LOGO_FONT_4X5 = LOGO_FONT_BANNER
LOGO_FONT_4X7 = LOGO_FONT_BANNER
LOGO_FONT_3X7 = LOGO_FONT_BANNER


def style_text(text: str, *codes: str) -> str:
    if not COLOR_ENABLED or not codes:
        return text
    return "".join(codes) + text + Ansi.RESET


def horizontal_rule(char: str = "-", width: int = 45) -> str:
    return char * width


def is_interactive_terminal() -> bool:
    stdin_tty = getattr(sys.stdin, "isatty", lambda: False)()
    stdout_tty = getattr(sys.stdout, "isatty", lambda: False)()
    return bool(stdin_tty and stdout_tty)


def clear_screen() -> None:
    if os.environ.get("TERM") or os.name != "nt":
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        return

    command = "cls" if os.name == "nt" else "clear"
    try:
        os.system(command)
    except Exception:
        pass


def configure_text_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def display_width(text: str) -> int:
    text = strip_ansi(text)
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


def pad_right(text: str, width: int) -> str:
    padding = width - display_width(text)
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
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                out.append(match.group(0))
                had_ansi = True
                i = match.end()
                continue

        ch = text[i]
        ch_w = display_width(ch)
        if width + ch_w > max_width:
            break
        out.append(ch)
        width += ch_w
        i += 1

    result = "".join(out)
    if had_ansi and COLOR_ENABLED and not result.endswith(Ansi.RESET):
        result += Ansi.RESET
    return result


def _take_suffix_by_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""

    tokens = []
    last = 0
    for match in ANSI_ESCAPE_RE.finditer(text):
        if match.start() > last:
            tokens.append(("text", text[last:match.start()]))
        tokens.append(("ansi", match.group(0)))
        last = match.end()
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
            ch_w = display_width(ch)
            if width + ch_w > max_width:
                result = "".join(reversed(out_rev))
                if had_ansi and COLOR_ENABLED and not result.endswith(Ansi.RESET):
                    result += Ansi.RESET
                return result
            out_rev.append(ch)
            width += ch_w

    result = "".join(reversed(out_rev))
    if had_ansi and COLOR_ENABLED and not result.endswith(Ansi.RESET):
        result += Ansi.RESET
    return result


def glyphs() -> dict:
    if os.environ.get("CSC_ASCII_UI"):
        return ASCII_GLYPHS
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        ("".join(UNICODE_GLYPHS.values())).encode(encoding)
        return UNICODE_GLYPHS
    except Exception:
        return ASCII_GLYPHS


def ellipsize_middle(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text

    ellipsis = glyphs().get("ellipsis", "...")
    if max_width <= display_width(ellipsis) + 1:
        return _take_prefix_by_width(text, max_width)

    prefix_w = (max_width - display_width(ellipsis)) // 2
    suffix_w = max_width - display_width(ellipsis) - prefix_w
    return _take_prefix_by_width(text, prefix_w) + ellipsis + _take_suffix_by_width(text, suffix_w)


def term_width(fallback: int = 90) -> int:
    try:
        if getattr(sys.stdout, "isatty", lambda: False)():
            try:
                return os.get_terminal_size(sys.stdout.fileno()).columns
            except Exception:
                pass
        return shutil.get_terminal_size(fallback=(fallback, 24)).columns
    except Exception:
        return fallback


def term_height(fallback: int = 24) -> int:
    try:
        if getattr(sys.stdout, "isatty", lambda: False)():
            try:
                return os.get_terminal_size(sys.stdout.fileno()).lines
            except Exception:
                pass
        return shutil.get_terminal_size(fallback=(90, fallback)).lines
    except Exception:
        return fallback


def tui_width(cols: Optional[int] = None, *, fallback: int = 90) -> int:
    cols = term_width(fallback=fallback) if cols is None else int(cols)
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


def _can_encode(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except Exception:
        return False


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
    max_width = term_width() if max_width is None else max(20, int(max_width))

    ascii_ui = bool(os.environ.get("CSC_ASCII_UI"))
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
                "CLONER",
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

        bottom_base = _max_w(session) + _max_w(cloner)
        bottom_max_gap = max_width - bottom_base
        if bottom_max_gap >= min_gap and _max_w(codex) <= max_width:
            bottom_gap = min(bottom_max_gap, _ideal_part_gap(min_gap=min_gap))
            bottom = _merge_horiz(session, cloner, gap=bottom_gap)
            stacked = _normalize_logo_block(codex) + _normalize_logo_block(bottom)
            if _max_w(stacked) <= max_width:
                return stacked

        if _max_w(codex) <= max_width and _max_w(session) <= max_width and _max_w(cloner) <= max_width:
            stacked = _normalize_logo_block(codex) + _normalize_logo_block(session) + _normalize_logo_block(cloner)
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

    full_text = "CODEX SESSION CLONER"
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
        "CSC",
        font=LOGO_FONT_3X7,
        fill=fill,
        shadow=shadow,
        max_width=max_width,
        char_gap=1,
        word_gap=2,
        shadow_ok=True,
        gradient=("#00FFFF", "#0000FF"),
    )
    short = "codex-session-cloner"
    segments = short.split("-")
    if COLOR_ENABLED and len(segments) == 3:
        seg_colors = (Ansi.BRIGHT_CYAN, Ansi.BRIGHT_MAGENTA, Ansi.BRIGHT_BLUE)
        dash = style_text("-", Ansi.DIM)
        short_line = dash.join(style_text(seg, Ansi.BOLD, color) for seg, color in zip(segments, seg_colors))
    else:
        short_line = short
    return _normalize_logo_block(acronym) + [ellipsize_middle(short_line, max_width)]


def align_line(line: str, width: int, *, center: bool) -> str:
    if not center:
        return line
    padding = (max(0, width - display_width(line))) // 2
    return (" " * padding) + line


def _box_chars() -> dict:
    if os.environ.get("CSC_ASCII_UI"):
        return ASCII_BOX_CHARS
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        ("".join(UNICODE_BOX_CHARS.values())).encode(encoding)
        return UNICODE_BOX_CHARS
    except Exception:
        return ASCII_BOX_CHARS


def render_box(lines, width: Optional[int] = None, border_codes: Optional[Tuple[str, ...]] = None) -> List[str]:
    cols = term_width()
    if width is None:
        width = min(cols, 90)
    width = min(cols, max(24, int(width)))
    inner = max(1, width - 4)

    box = _box_chars()
    top = box["tl"] + (box["h"] * (width - 2)) + box["tr"]
    bottom = box["bl"] + (box["h"] * (width - 2)) + box["br"]

    out = [style_text(top, *(border_codes or ()))]
    for line in lines:
        text = pad_right(ellipsize_middle(str(line), inner), inner)
        if border_codes:
            left = style_text(box["v"], *border_codes)
            right = style_text(box["v"], *border_codes)
            row = f"{left} {text} {right}"
        else:
            row = f"{box['v']} {text} {box['v']}"

        if COLOR_ENABLED:
            row += Ansi.RESET
        out.append(row)

    bottom_line = style_text(bottom, *(border_codes or ()))
    if COLOR_ENABLED:
        bottom_line += Ansi.RESET
    out.append(bottom_line)
    return out


def read_key(timeout_ms: Optional[int] = None) -> Optional[str]:
    if os.name == "nt":
        try:
            import msvcrt
        except Exception:
            return None

        if timeout_ms is not None:
            deadline = time.monotonic() + max(0, timeout_ms) / 1000
            while not msvcrt.kbhit():
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.01)

        first = msvcrt.getwch()
        if first in ("\r", "\n"):
            return "ENTER"
        if first == "\x03":
            raise KeyboardInterrupt
        if first in ("\x00", "\xe0"):
            second = msvcrt.getwch()
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
                "I": "PAGE_UP",
                "Q": "PAGE_DOWN",
            }.get(second)
        if first == "\x1b":
            return "ESC"
        return first

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
        timeout = None if timeout_ms is None else max(0, timeout_ms) / 1000
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
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
                    if ch3 in (b"5", b"6") and select.select([fd], [], [], 0.05)[0]:
                        ch4 = os.read(fd, 1)
                        if ch4 == b"~":
                            return {
                                b"5": "PAGE_UP",
                                b"6": "PAGE_DOWN",
                            }.get(ch3, "ESC")
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
