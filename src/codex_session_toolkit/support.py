"""Codex-specific helpers that wrap shared ``core`` primitives.

Tool-agnostic primitives (atomic_write, file_lock, replace_with_retry,
prune_old_backups, long-path, safe_copy2, ensure_path_within_dir,
nearest_existing_parent) live in ``core.support`` so cc-clean and future
sibling tools can reuse them. Codex-specific helpers — bundle root resolution,
machine identity, ISO timestamp helpers, session classification — stay here.

For backwards compatibility every primitive that used to live in this module
is re-exported below; existing callers do not need to update their imports.
"""

from __future__ import annotations

import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .core.support import (
    PathEscapeError,
    atomic_write,
    file_lock,
    long_path,
    lock_path_for,
    nearest_existing_parent,
    prune_old_backups,
    replace_with_retry,
    safe_copy2,
)
from .core.support import ensure_path_within_dir as _core_ensure_path_within_dir
from .errors import ToolkitError
from .paths import CodexPaths


# ---------------------------------------------------------------------------
# Backwards-compatible aliases for symbols that used to live here
# ---------------------------------------------------------------------------

# Old name; existing call sites import _long_path. Keep the alias so PR diffs
# stay focused on real changes — PR 3 will migrate callers to ``long_path``.
_long_path = long_path


def ensure_path_within_dir(target_path: Path, base_dir: Path, label: str) -> None:
    """Wrap ``core.support.ensure_path_within_dir`` re-raising as ``ToolkitError``.

    The Codex CLI surface promises ``ToolkitError`` everywhere; the core
    primitive raises ``PathEscapeError`` so it can stay tool-agnostic. We
    bridge the two with a thin shim.
    """
    try:
        _core_ensure_path_within_dir(target_path, base_dir, label)
    except PathEscapeError as exc:
        raise ToolkitError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Codex-specific timestamp helpers
# ---------------------------------------------------------------------------


def extract_iso_timestamp(raw_value: str) -> str:
    if not raw_value:
        return ""
    match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", raw_value)
    return match.group(0) if match else ""


def normalize_iso(raw_value: str) -> str:
    return extract_iso_timestamp(raw_value)


def iso_to_epoch(raw_value: str) -> int:
    normalized = normalize_iso(raw_value)
    if not normalized:
        return 0
    try:
        return int(datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def export_batch_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


# ---------------------------------------------------------------------------
# Codex-specific machine identity + bundle root helpers
# ---------------------------------------------------------------------------


def machine_label_to_key(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", (label or "").strip()).strip("-._")
    return normalized or "unknown-machine"


def detect_machine_label() -> str:
    raw = (
        os.environ.get("CST_MACHINE_LABEL")
        or os.environ.get("CSC_MACHINE_LABEL")
        or os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or platform.node()
        or "unknown-machine"
    )
    return raw.strip() or "unknown-machine"


def detect_machine_key() -> str:
    return machine_label_to_key(detect_machine_label())


def build_machine_bundle_root(bundle_root: Path, machine_key: Optional[str] = None) -> Path:
    resolved_key = machine_key or detect_machine_key()
    return Path(bundle_root).expanduser() / resolved_key


def build_single_export_root(bundle_root: Path, machine_key: Optional[str] = None) -> Path:
    return build_machine_bundle_root(bundle_root, machine_key) / "single" / export_batch_slug()


def build_batch_export_root(bundle_root: Path, archive_group: str, machine_key: Optional[str] = None) -> Path:
    return build_machine_bundle_root(bundle_root, machine_key) / archive_group / export_batch_slug()


def classify_session_kind(source_name: str, originator_name: str) -> str:
    if source_name == "vscode":
        return "desktop"
    if source_name == "cli":
        return "cli"
    if "Desktop" in originator_name:
        return "desktop"
    if originator_name in {"codex_cli_rs", "codex-tui"} or originator_name.startswith("codex_cli"):
        return "cli"
    return "unknown"


def restrict_to_local_bundle_workspace(paths: CodexPaths, target_path: Path, label: str) -> Path:
    workspace = paths.local_bundle_workspace.expanduser()
    target_path = Path(target_path).expanduser()
    ensure_path_within_dir(target_path, workspace, label)
    return target_path


def normalize_bundle_root(
    paths: CodexPaths,
    bundle_root: Optional[Path],
    default_root: Path,
    *,
    label: str = "Bundle root",
) -> Path:
    target_root = Path(bundle_root or default_root).expanduser()
    return restrict_to_local_bundle_workspace(paths, target_root, label)


def backup_file(code_dir: Path, backup_root: Path, backed_up: set[str], path: Path, *, enabled: bool) -> None:
    if not enabled or not path.exists():
        return
    resolved = str(path.resolve())
    if resolved in backed_up:
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / path.relative_to(code_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_copy2(path, target)
    backed_up.add(resolved)


__all__ = [
    # Re-exported from core.support (tool-agnostic primitives)
    "PathEscapeError",
    "atomic_write",
    "ensure_path_within_dir",
    "file_lock",
    "long_path",
    "lock_path_for",
    "nearest_existing_parent",
    "prune_old_backups",
    "replace_with_retry",
    "safe_copy2",
    # Codex-specific helpers (this module)
    "_long_path",
    "backup_file",
    "build_batch_export_root",
    "build_machine_bundle_root",
    "build_single_export_root",
    "classify_session_kind",
    "detect_machine_key",
    "detect_machine_label",
    "export_batch_slug",
    "extract_iso_timestamp",
    "iso_to_epoch",
    "machine_label_to_key",
    "normalize_bundle_root",
    "normalize_iso",
    "restrict_to_local_bundle_workspace",
]
