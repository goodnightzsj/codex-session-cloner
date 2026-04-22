"""Shared utility helpers."""

from __future__ import annotations

import os
import platform
import re
import shutil
import tempfile
import time
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
    lock_path: Optional[Path] = None,
) -> Iterator[TextIO]:
    """Yield a text file handle that is atomically moved over ``path`` on successful close.

    The temporary file lives in ``path.parent`` (same filesystem → ``os.replace`` is atomic).
    If the caller raises or the final replace fails, the temp file is unlinked and the
    exception re-raised so the original ``path`` is never left half-written.

    When ``lock_path`` is provided, an advisory ``file_lock`` is held for the duration
    of the write so concurrent writers cannot interleave or clobber each other.
    Callers that already hold the lock externally (e.g. for read-modify-write
    sequences) MUST pass ``lock_path=None`` to avoid self-deadlock — ``file_lock``
    is not reentrant across separate fds on POSIX.

    On Windows ``os.replace`` can fail with ``PermissionError`` if the destination
    is briefly held open by indexer/AV scanners; we retry a few times before
    surfacing the error so transient locks don't surface as user-visible failures.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    # newline="" disables Python's universal-newlines translation on Windows.
    # Codex CLI (Rust) writes rollout / history / session_index JSONL with LF-only;
    # without this, text-mode write would emit CRLF on Windows and corrupt byte-
    # level comparisons (importing.py compares read_bytes()) and mix line endings
    # for tools that later re-open the file on Windows.
    fh = os.fdopen(tmp_fd, "w", encoding=encoding, newline="")
    lock_ctx = file_lock(lock_path) if lock_path is not None else _null_context()
    try:
        with lock_ctx:
            yield fh
            fh.close()
            replace_with_retry(tmp_path, str(path))
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
def _null_context() -> Iterator[None]:
    yield


def replace_with_retry(src: str, dst: str, *, attempts: int = 5, base_delay: float = 0.02) -> None:
    """``os.replace`` with bounded retry to absorb transient Windows ``PermissionError``.

    Windows briefly holds files open behind us (indexer, AV scanners, IDE file
    watchers); a single ``os.replace`` may race that hold and surface as a
    user-visible failure. Retry with exponential backoff (5 attempts ≈ 620 ms
    cumulative max) covers the typical transient window.

    On POSIX a ``PermissionError`` from ``os.replace`` indicates a real
    permission/ownership problem — there is no transient AV-style holder — so
    we re-raise immediately rather than burn ~620 ms before failing visibly.
    """
    if os.name != "nt":
        os.replace(src, dst)
        return

    last_exc: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(base_delay * (2 ** attempt))
    if last_exc is not None:
        raise last_exc


def lock_path_for(target_path: Path) -> Path:
    """Canonical lock-file path for any data file (``<path>.lock``).

    Centralised so all writers of the same shared file (session_index.jsonl,
    history.jsonl, state.json, ...) end up serialising on the SAME lock file
    even when they live in different stores/services modules.
    """
    target_path = Path(target_path)
    return target_path.with_suffix(target_path.suffix + ".lock")


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

    # On case-insensitive filesystems (Windows NTFS, macOS APFS-default) the
    # raw string compare would mark `C:\Users\Foo` and `c:\users\foo` as
    # distinct, falsely flagging legitimate paths as "escapes". normcase is a
    # no-op on Linux so POSIX behavior is unchanged.
    if os.path.normcase(common) == os.path.normcase(base_real):
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
