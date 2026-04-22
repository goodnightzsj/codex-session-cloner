"""Tool-agnostic shared infrastructure.

This package holds primitives that have no Codex-specific knowledge so
sibling tools (claude/cc-clean, future agents) can share the same battle-
tested code without duplicating it. Anything here MUST NOT import from
``codex_session_toolkit`` siblings — only stdlib and other ``core`` modules.

Layered responsibilities:
* ``core.support``   — atomic file I/O, advisory locking, Windows-safe paths.
* ``core.tui``       — platform-agnostic terminal primitives.
* ``core.launcher_env`` — environment-variable conventions for OS launchers.
"""
