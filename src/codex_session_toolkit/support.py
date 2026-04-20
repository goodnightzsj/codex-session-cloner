"""Shared utility helpers."""

from __future__ import annotations

import os
import platform
import re
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, TextIO

from .errors import ToolkitError
from .paths import CodexPaths


try:
    import fcntl as _fcntl  # POSIX
except ImportError:
    _fcntl = None

try:
    import msvcrt as _msvcrt  # Windows
except ImportError:
    _msvcrt = None


@contextmanager
def atomic_write(
    path: Path,
    *,
    encoding: str = "utf-8",
) -> Iterator[TextIO]:
    """Yield a text file handle that is atomically moved over ``path`` on successful close.

    The temporary file lives in ``path.parent`` (same filesystem → ``os.replace`` is atomic).
    If the caller raises or the final replace fails, the temp file is unlinked and the
    exception re-raised so the original ``path`` is never left half-written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    fh = os.fdopen(tmp_fd, "w", encoding=encoding)
    try:
        yield fh
        fh.close()
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            fh.close()
        except Exception:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an advisory exclusive file lock for the duration of the context.

    Uses ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows. On platforms
    where neither is available the lock is a no-op (best-effort degrade).
    The lock file is created next to the resource (``<path>.lock``) and persists;
    its presence between invocations is harmless.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        elif _msvcrt is not None:
            # LK_LOCK blocks up to ~10s then raises; acceptable for short index writes.
            _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            elif _msvcrt is not None:
                try:
                    _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        os.close(fd)


def prune_old_backups(backup_parent: Path, *, keep_last: int = 20) -> list[Path]:
    """Remove oldest ``repair_backups/*`` subdirs once count exceeds ``keep_last``.

    Sort by mtime ascending, delete from the oldest end until only ``keep_last``
    remain. Returns the list of removed paths for logging/tests. Missing parent
    is a no-op. Errors on individual removals are suppressed so cleanup never
    blocks the caller's primary operation.
    """
    if not backup_parent.exists() or not backup_parent.is_dir():
        return []
    children = [c for c in backup_parent.iterdir() if c.is_dir()]
    if len(children) <= keep_last:
        return []
    children.sort(key=lambda p: p.stat().st_mtime)
    removed: list[Path] = []
    for old in children[:-keep_last]:
        try:
            shutil.rmtree(old, ignore_errors=True)
            removed.append(old)
        except OSError:
            pass
    return removed


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


def _long_path(path: "os.PathLike[str] | str") -> str:
    """Return a path string that survives Windows MAX_PATH (260) when used via Win32 APIs.

    On non-Windows platforms, this is a no-op. On Windows, paths longer than MAX_PATH are
    prefixed with ``\\\\?\\`` (or ``\\\\?\\UNC\\`` for UNC roots), which tells Win32 APIs
    to accept up to ~32K characters and bypasses the legacy limit even on installations
    without ``LongPathsEnabled``.
    """
    text = os.fspath(path)
    if os.name != "nt":
        return text
    if text.startswith("\\\\?\\"):
        return text
    absolute = os.path.abspath(text)
    if len(absolute) < 248:
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def safe_copy2(src: Path, dst: Path) -> None:
    """``shutil.copy2`` wrapper that tolerates long destination paths on Windows."""
    shutil.copy2(_long_path(src), _long_path(dst))


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
