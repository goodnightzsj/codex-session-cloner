"""Environment-variable conventions launchers must establish before exec'ing Python.

Both the codex and claude launchers (`.cmd` / `.ps1` / shell) are expected to
seed the same UTF-8 + locale invariants so the Python process starts in a
predictable encoding regardless of the host's default codepage.

This module is intentionally tiny — it only exposes constants and a documented
checklist. The launcher scripts hard-code the same names because they run
*before* Python is invoked, so they cannot import this module. Keeping the
canonical list here lets us:

* document the rationale in one place,
* assert in tests that the launchers stay in sync with the spec,
* let in-process code (e.g. a `doctor` subcommand) verify the env was set.
"""

from __future__ import annotations

import os
from typing import Mapping


# Names + recommended values. Launchers MUST set these unless the user has
# already exported them — they should treat these as defaults, not overrides.
LAUNCHER_ENV_DEFAULTS: Mapping[str, str] = {
    # Enable Python 3.7+ UTF-8 mode: forces sys.stdin/stdout to UTF-8 and makes
    # filesystem encoding UTF-8 even when LANG is C/POSIX or the Windows
    # codepage is cp936/cp1252.
    "PYTHONUTF8": "1",
    # Belt-and-braces: covers older interpreters and code paths that bypass
    # PYTHONUTF8 (e.g. opening files via low-level wrappers that respect this
    # var explicitly).
    "PYTHONIOENCODING": "utf-8",
}


def env_was_seeded(env: Mapping[str, str] = None) -> bool:
    """Return True when every defaulted variable is present (and non-empty).

    Useful from a ``doctor`` / ``--diagnose`` command to detect users who
    bypassed the launcher (e.g. invoked ``python -m codex_session_toolkit``
    directly) and may hit codec issues on Windows.
    """
    env_map = os.environ if env is None else env
    return all(env_map.get(name) for name in LAUNCHER_ENV_DEFAULTS)


__all__ = ["LAUNCHER_ENV_DEFAULTS", "env_was_seeded"]
