"""Shared utility helpers."""

from __future__ import annotations

import os
import platform
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import ToolkitError
from .paths import CodexPaths


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


def ensure_path_within_dir(target_path: Path, base_dir: Path, label: str) -> None:
    try:
        target_real = os.path.realpath(target_path)
        base_real = os.path.realpath(base_dir)
        common = os.path.commonpath([target_real, base_real])
    except ValueError:
        common = ""

    if common == base_real:
        return

    raise ToolkitError(f"{label} escapes base directory: {target_path}")


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


def nearest_existing_parent(path_str: str) -> str:
    if not path_str:
        return ""
    path = Path(path_str).expanduser()
    while True:
        if path.exists():
            return str(path)
        if path.parent == path:
            return ""
        path = path.parent


def backup_file(code_dir: Path, backup_root: Path, backed_up: set[str], path: Path, *, enabled: bool) -> None:
    if not enabled or not path.exists():
        return
    resolved = str(path.resolve())
    if resolved in backed_up:
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / path.relative_to(code_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    backed_up.add(resolved)
