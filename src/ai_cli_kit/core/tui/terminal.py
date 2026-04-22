"""Tool-agnostic terminal primitives.

Extracted from ``codex_session_toolkit.tui.terminal`` so cc-clean (and any
future sibling tool) can share the same Windows-VT bootstrap, ANSI styling,
display-width math, box drawing, and key-reading code without forking it.

What lives **here** (this module):
* ANSI escape constants, color detection, Windows VT enable.
* Box / glyph tables and the encoding-aware fallback to ASCII.
* ``display_width`` (CJK + ANSI aware), ``pad_right``, ``ellipsize_middle``.
* ``term_width`` / ``term_height`` / ``clear_screen`` / ``configure_text_streams``.
* ``read_key`` (POSIX termios + Windows msvcrt with arrow-key normalisation).
* ``render_box``.

What stays **out** of here (tool-specific):
* Logo wordmarks (each tool has its own brand banner).
* ``tui_width`` (each tool has different layout caps; trivial wrapper).
* App-specific menus, screens, and event loops.

ASCII fallback env names: we honour every prefix any sibling tool uses
(``CCC_*`` for cc-clean, ``CST_*``/``CSC_*`` for codex-session-toolkit), so
``glyphs()`` and ``_box_chars()`` produce identical results across tools when
the user sets a single env override anywhere.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
import unicodedata
from typing import List, Optional


# ---------------------------------------------------------------------------
# ANSI / VT bootstrap
# ---------------------------------------------------------------------------


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


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _enable_windows_vt_mode() -> bool:
    """Best-effort enable VT (ANSI escape) processing on Windows 10+ consoles.

    Without this, plain ``cmd.exe``/``conhost`` print ``\\033[…m`` as literal
    garbage. Returns True if VT is enabled (or already was), False otherwise.
    Safe to call from non-Windows or when stdout is redirected.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle == 0 or handle == INVALID_HANDLE_VALUE:
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


_WINDOWS_VT_OK = _enable_windows_vt_mode()


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term.lower() == "dumb":
        return False
    if os.name == "nt" and not _WINDOWS_VT_OK:
        return False
    return True


COLOR_ENABLED = supports_color()


# ---------------------------------------------------------------------------
# Box drawing + glyph tables
# ---------------------------------------------------------------------------


UNICODE_BOX_CHARS = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}
ASCII_BOX_CHARS = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}

UNICODE_GLYPHS = {
    "pointer": "›",
    "ellipsis": "…",
    "danger": "✗",
    "warn": "⚠",
    "ok": "✓",
}
ASCII_GLYPHS = {
    "pointer": ">",
    "ellipsis": "...",
    "danger": "!!",
    "warn": "!",
    "ok": "ok",
}

# Honour every sibling tool's ASCII override env so a single user setting
# coerces all tools to the ASCII fallback in one go.
ASCII_UI_ENV_NAMES = ("CCC_ASCII_UI", "CC_CLEAN_ASCII_UI", "CST_ASCII_UI", "CSC_ASCII_UI")


def env_first(*names: str) -> str:
    """Return the first non-empty value among ``names`` from ``os.environ``."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


# Backwards-compat alias for callers that imported the underscore-prefixed name.
_env_first = env_first


def _can_encode(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except Exception:
        return False


def glyphs() -> dict:
    if env_first(*ASCII_UI_ENV_NAMES):
        return ASCII_GLYPHS
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        ("".join(UNICODE_GLYPHS.values())).encode(encoding)
        return UNICODE_GLYPHS
    except Exception:
        return ASCII_GLYPHS


def _box_chars() -> dict:
    if env_first(*ASCII_UI_ENV_NAMES):
        return ASCII_BOX_CHARS
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        ("".join(UNICODE_BOX_CHARS.values())).encode(encoding)
        return UNICODE_BOX_CHARS
    except Exception:
        return ASCII_BOX_CHARS


# ---------------------------------------------------------------------------
# Styling primitives
# ---------------------------------------------------------------------------


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
    """Clear the terminal preferring ANSI; fall back to ``os.system('cls')`` on legacy Windows.

    The ANSI path is faster (no subprocess spawn) and avoids the visible flicker
    that ``cls`` causes. When VT initialisation failed AND no ``TERM`` env hints
    at a VT-capable shell we fall back to ``cls`` so even ancient consoles get a
    clean screen.
    """
    if os.name != "nt" or _WINDOWS_VT_OK or os.environ.get("TERM"):
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        return

    try:
        os.system("cls")
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


# ---------------------------------------------------------------------------
# Display-width math (ANSI + CJK aware)
# ---------------------------------------------------------------------------


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


def align_line(line: str, width: int, *, center: bool) -> str:
    if not center:
        return line
    padding = (max(0, width - display_width(line))) // 2
    return (" " * padding) + line


# ---------------------------------------------------------------------------
# Terminal size + box rendering
# ---------------------------------------------------------------------------


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


def render_box(lines, width: Optional[int] = None, border_codes: Optional[tuple] = None) -> List[str]:
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


# ---------------------------------------------------------------------------
# Keyboard input (cross-platform)
# ---------------------------------------------------------------------------


def read_key(timeout_ms: Optional[int] = None) -> Optional[str]:
    """Read a single key press normalised to a token.

    Returns one of: ``"UP"``, ``"DOWN"``, ``"LEFT"``, ``"RIGHT"``,
    ``"PAGE_UP"``, ``"PAGE_DOWN"``, ``"ENTER"``, ``"ESC"``, or the literal
    typed character. Returns ``None`` when ``timeout_ms`` elapses with no
    input. ``timeout_ms=None`` blocks indefinitely (preferred when paired
    with SIGWINCH so resize wakes the read).
    """
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


__all__ = [
    "ANSI_ESCAPE_RE",
    "ASCII_BOX_CHARS",
    "ASCII_GLYPHS",
    "ASCII_UI_ENV_NAMES",
    "Ansi",
    "COLOR_ENABLED",
    "UNICODE_BOX_CHARS",
    "UNICODE_GLYPHS",
    "_box_chars",
    "_can_encode",
    "_env_first",
    "_take_prefix_by_width",
    "_take_suffix_by_width",
    "align_line",
    "clear_screen",
    "configure_text_streams",
    "display_width",
    "ellipsize_middle",
    "env_first",
    "glyphs",
    "horizontal_rule",
    "is_interactive_terminal",
    "pad_right",
    "read_key",
    "render_box",
    "strip_ansi",
    "style_text",
    "supports_color",
    "term_height",
    "term_width",
]
