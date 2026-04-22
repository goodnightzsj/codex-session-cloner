"""Path helpers for Claude local cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ClaudePaths:
    home: Path
    claude_dir: Path
    state_file: Path
    settings_file: Path
    credentials_file: Path
    telemetry_dir: Path
    statsig_dir: Path
    projects_dir: Path
    history_file: Path
    sessions_dir: Path
    session_env_dir: Path
    claude_backups_dir: Path
    backup_root_base: Path


def default_paths(home: Optional[Path] = None) -> ClaudePaths:
    home_dir = Path.home() if home is None else Path(home).expanduser()
    claude_dir = home_dir / ".claude"
    return ClaudePaths(
        home=home_dir,
        claude_dir=claude_dir,
        state_file=home_dir / ".claude.json",
        settings_file=claude_dir / "settings.json",
        credentials_file=claude_dir / ".credentials.json",
        telemetry_dir=claude_dir / "telemetry",
        statsig_dir=claude_dir / "statsig",
        projects_dir=claude_dir / "projects",
        history_file=claude_dir / "history.jsonl",
        sessions_dir=claude_dir / "sessions",
        session_env_dir=claude_dir / "session-env",
        claude_backups_dir=claude_dir / "backups",
        backup_root_base=home_dir / ".claude-clean-backups",
    )
