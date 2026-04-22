"""ai-cli-kit — unified toolbox for AI CLI agents (Codex, Claude Code, ...).

Holds three sibling subpackages:

* :mod:`ai_cli_kit.core` — tool-agnostic primitives (atomic I/O, locks,
  terminal/TUI helpers, screen-mode detection, launcher env contract).
* :mod:`ai_cli_kit.codex` — Codex CLI session toolkit (clone / export /
  import / repair). Historically published as ``codex-session-toolkit``.
* :mod:`ai_cli_kit.claude` — Claude Code local cleanup (identifier scrub,
  telemetry purge, history remap). Historically published as ``cc-clean``.

The top-level :mod:`ai_cli_kit.cli` (added in PR 4) dispatches into the
right subpackage based on ``aik <tool> <subcommand>``. Both legacy entry
points (``codex-session-toolkit`` / ``cst`` and ``cc-clean``) remain wired
for backwards compatibility.
"""

APP_DISPLAY_NAME = "AI CLI Kit"
APP_COMMAND = "aik"
__version__ = "0.2.0"
