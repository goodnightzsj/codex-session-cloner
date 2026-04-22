"""Screen mode selection for the cc-clean TUI."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Mapping, Optional, TextIO


MAIN_ENTER_SEQUENCE = "\033[?25l\033[H\033[2J"
MAIN_EXIT_SEQUENCE = "\033[2J\033[H\033[?25h"
ALT_ENTER_FALLBACK = "\033[?1049h"
ALT_EXIT_FALLBACK = "\033[?1049l"
SCREEN_MODE_ENV_NAMES = ("CCC_TUI_SCREEN", "CC_CLEAN_TUI_SCREEN")
KNOWN_MAIN_TERM_PROGRAMS = {"Apple_Terminal", "iTerm.app", "vscode"}
KNOWN_ALT_TERM_PROGRAMS = {"ghostty", "WezTerm"}
SCREEN_MODE_VALUES = {"auto", "main", "alt"}


@dataclass(frozen=True)
class TerminfoScreenCaps:
    supports_alt_screen: bool
    non_rev_rmcup: bool
    enter_alt: str = ""
    leave_alt: str = ""


@dataclass(frozen=True)
class ScreenModeDecision:
    requested: str
    resolved: str
    reason: str
    enter_sequence: str
    exit_sequence: str

    @property
    def label(self) -> str:
        if self.requested == self.resolved:
            return self.resolved
        return "%s via %s" % (self.resolved, self.requested)


def _env_first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return ""


def normalize_screen_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SCREEN_MODE_VALUES:
        return normalized
    return "auto"


def _stream_is_tty(stream: TextIO) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)())


def detect_terminfo_screen_caps(
    env: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
) -> TerminfoScreenCaps:
    env_map = os.environ if env is None else env
    stream = sys.stdout if stdout is None else stdout
    term = env_map.get("TERM", "")
    if not term or not _stream_is_tty(stream):
        return TerminfoScreenCaps(False, False)

    try:
        import curses
    except Exception:
        return TerminfoScreenCaps(False, False)

    try:
        fd = stream.fileno()
    except Exception:
        fd = -1

    try:
        curses.setupterm(term=term, fd=fd)
        smcup = curses.tigetstr("smcup") or b""
        rmcup = curses.tigetstr("rmcup") or b""
        nrrmc = curses.tigetflag("nrrmc") == 1
        return TerminfoScreenCaps(
            supports_alt_screen=bool(smcup and rmcup),
            non_rev_rmcup=bool(nrrmc),
            enter_alt=smcup.decode("latin1", errors="ignore"),
            leave_alt=rmcup.decode("latin1", errors="ignore"),
        )
    except Exception:
        return TerminfoScreenCaps(False, False)


def query_tmux_alternate_screen(env: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    env_map = os.environ if env is None else env
    if not env_map.get("TMUX"):
        return None

    try:
        result = subprocess.run(
            ["tmux", "show-options", "-gv", "alternate-screen"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    value = result.stdout.strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    return None


def _prefers_main_screen(env: Mapping[str, str]) -> bool:
    term_program = env.get("TERM_PROGRAM", "")
    if term_program in KNOWN_MAIN_TERM_PROGRAMS:
        return True
    if env.get("VSCODE_INJECTION") == "1":
        return True
    return False


def _supports_alt_by_profile(env: Mapping[str, str]) -> bool:
    term_program = env.get("TERM_PROGRAM", "")
    term = env.get("TERM", "").lower()
    if env.get("KITTY_WINDOW_ID") or "kitty" in term:
        return True
    if env.get("VTE_VERSION"):
        return True
    if env.get("WT_SESSION"):
        return True
    if term_program in KNOWN_ALT_TERM_PROGRAMS:
        return True
    if term.startswith("xterm") and not _prefers_main_screen(env):
        return True
    if term.startswith("tmux") or term.startswith("screen"):
        return True
    return False


def _main_screen_decision(requested: str, reason: str) -> ScreenModeDecision:
    return ScreenModeDecision(
        requested=requested,
        resolved="main",
        reason=reason,
        enter_sequence=MAIN_ENTER_SEQUENCE,
        exit_sequence=MAIN_EXIT_SEQUENCE,
    )


def _alt_screen_decision(requested: str, reason: str, caps: TerminfoScreenCaps) -> ScreenModeDecision:
    enter_alt = caps.enter_alt or ALT_ENTER_FALLBACK
    leave_alt = caps.leave_alt or ALT_EXIT_FALLBACK
    return ScreenModeDecision(
        requested=requested,
        resolved="alt",
        reason=reason,
        enter_sequence=enter_alt + "\033[?25l\033[H",
        exit_sequence=leave_alt + "\033[?25h",
    )


def resolve_screen_mode(
    *,
    requested: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
    terminfo_caps: Optional[TerminfoScreenCaps] = None,
    tmux_alt_screen: Optional[bool] = None,
) -> ScreenModeDecision:
    env_map = os.environ if env is None else env
    stream = sys.stdout if stdout is None else stdout
    requested_mode = normalize_screen_mode(requested or _env_first(env_map, *SCREEN_MODE_ENV_NAMES))
    caps = detect_terminfo_screen_caps(env_map, stream) if terminfo_caps is None else terminfo_caps

    if requested_mode == "main":
        return _main_screen_decision(requested_mode, "forced by environment override")
    if requested_mode == "alt":
        return _alt_screen_decision(requested_mode, "forced by environment override", caps)

    if not _stream_is_tty(stream):
        return _main_screen_decision(requested_mode, "stdout is not a TTY")

    term = env_map.get("TERM", "").strip().lower()
    if not term or term == "dumb":
        return _main_screen_decision(requested_mode, "TERM does not support full-screen control")

    if not caps.supports_alt_screen:
        return _main_screen_decision(requested_mode, "terminfo has no smcup/rmcup pair")

    if caps.non_rev_rmcup:
        return _main_screen_decision(requested_mode, "terminfo marks rmcup as non-reversible")

    tmux_allows_alt = query_tmux_alternate_screen(env_map) if tmux_alt_screen is None else tmux_alt_screen
    if tmux_allows_alt is False:
        return _main_screen_decision(requested_mode, "tmux alternate-screen is disabled")

    if _prefers_main_screen(env_map):
        return _main_screen_decision(requested_mode, "terminal profile commonly overrides alternate-screen behavior")

    if _supports_alt_by_profile(env_map):
        return _alt_screen_decision(requested_mode, "terminal profile is a good fit for alternate-screen", caps)

    return _main_screen_decision(requested_mode, "fallback to the conservative main-screen path")
