"""Shared dataclasses for cleanup planning and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CleanupTarget:
    key: str
    label: str
    description: str
    action: str
    target_path: str
    json_fields: Tuple[str, ...] = ()
    env_keys: Tuple[str, ...] = ()
    default_selected: bool = False
    danger: bool = False
    may_remove_sessions: bool = False


@dataclass(frozen=True)
class PlanItem:
    target: CleanupTarget
    selected: bool
    exists: bool
    applicable: bool
    size_bytes: int
    details: str
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RunOptions:
    backup_enabled: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class ExecutionRecord:
    key: str
    status: str
    message: str
    backup_path: Optional[str] = None


@dataclass(frozen=True)
class ExecutionSummary:
    records: Tuple[ExecutionRecord, ...]
    backup_root: Optional[str] = None
