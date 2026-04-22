"""Top-level ``aik`` dispatcher (placeholder; full router lands in PR 4).

PR 3 wires the ``aik`` console-script in pyproject so ``pip install`` does not
error on a missing entry-point, but the routing logic — ``aik codex …`` /
``aik claude …`` / interactive hub — is fleshed out in PR 4. Until then, the
stub directs users to the per-tool entry points that already work
(``codex-session-toolkit`` / ``cst`` / ``cc-clean``).
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from . import APP_DISPLAY_NAME, __version__


_PLACEHOLDER_TEXT = """\
{name} {version} — top-level dispatcher pending PR 4.

While the unified ``aik <tool> <subcommand>`` router is being assembled, use
the per-tool entry points instead:

  codex-session-toolkit …    # Codex CLI session toolkit
  cst …                      # short alias for the same
  cc-clean …                 # Claude Code local cleanup

The full ``aik`` interface (with interactive hub) ships in the next PR.
"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is not None:
        # Honour any args the caller passed even though we don't dispatch yet,
        # so the help text is still surfaced clearly when invoked from a script.
        del argv
    sys.stdout.write(_PLACEHOLDER_TEXT.format(name=APP_DISPLAY_NAME, version=__version__))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
