"""Path helpers for Codex session data and local bundle workspaces."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CodexPaths:
    home: Path = field(default_factory=Path.home)
    cwd: Path = field(default_factory=Path.cwd)

    @property
    def code_dir(self) -> Path:
        return self.home / ".codex"

    @property
    def sessions_dir(self) -> Path:
        return self.code_dir / "sessions"

    @property
    def archived_sessions_dir(self) -> Path:
        return self.code_dir / "archived_sessions"

    @property
    def history_file(self) -> Path:
        return self.code_dir / "history.jsonl"

    @property
    def index_file(self) -> Path:
        return self.code_dir / "session_index.jsonl"

    @property
    def state_file(self) -> Path:
        return self.code_dir / ".codex-global-state.json"

    @property
    def config_file(self) -> Path:
        return self.code_dir / "config.toml"

    @property
    def local_bundle_workspace(self) -> Path:
        return self.cwd / "codex_sessions"

    @property
    def default_bundle_root(self) -> Path:
        return self.local_bundle_workspace

    @property
    def default_desktop_bundle_root(self) -> Path:
        return self.local_bundle_workspace

    @property
    def legacy_bundle_root(self) -> Path:
        return self.local_bundle_workspace / "bundles"

    @property
    def legacy_desktop_bundle_root(self) -> Path:
        return self.local_bundle_workspace / "desktop_bundles"

    @functools.lru_cache(maxsize=1)
    def latest_state_db(self) -> Optional[Path]:
        matches = sorted(self.code_dir.glob("state_*.sqlite"))
        return matches[-1] if matches else None
