"""Tool-agnostic file I/O, locking, and path safety primitives.

These helpers were extracted from ``codex_session_toolkit.support`` so
``cc-clean`` (and future sibling tools) can share the same atomic-write,
advisory-lock, Windows-long-path, and case-insensitive-FS logic without
forking it. **No Codex-specific knowledge** lives here.

Invariants worth preserving when modifying:
* ``atomic_write`` opens its tempfile with ``newline=""`` so JSONL writers
  do not silently translate ``\\n`` → ``\\r\\n`` on Windows text mode.
* ``file_lock`` is **not reentrant** across separate fds on POSIX. Callers
  doing read-modify-write must pass ``lock_path=None`` to ``atomic_write``
  to avoid self-deadlock.
* ``replace_with_retry`` only retries on Windows — POSIX ``PermissionError``
  on ``os.replace`` is a real ownership problem, not a transient AV race.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, TextIO

try:
    import fcntl as _fcntl  # POSIX
except ImportError:
    _fcntl = None

try:
    import msvcrt as _msvcrt  # Windows
except ImportError:
    _msvcrt = None


class PathEscapeError(Exception):
    """Raised when a path resolves outside its expected base directory.

    Defined here (not in a sibling ``errors`` module) so this package stays
    importable without any tool-specific dependency.
    """


# ---------------------------------------------------------------------------
# Atomic write + retrying replace
# ---------------------------------------------------------------------------


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
    # Critical for JSONL files written by Rust/Go tools that expect LF-only;
    # without this, byte-for-byte readers would see CRLF mismatches on Windows.
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

    Both paths are wrapped with :func:`long_path` so callers passing a path
    that exceeds Windows MAX_PATH (260 chars) — common when users install the
    toolkit deep under ``%USERPROFILE%\\OneDrive\\…`` — get the ``\\\\?\\``
    prefix that lets ``CreateFileW``-backed APIs (which include rename) cross
    the legacy limit. ``long_path`` is a no-op on POSIX so behaviour is
    unchanged there.
    """
    src_long = long_path(src)
    dst_long = long_path(dst)

    if os.name != "nt":
        os.replace(src_long, dst_long)
        return

    last_exc: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            os.replace(src_long, dst_long)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(base_delay * (2 ** attempt))
    if last_exc is not None:
        raise last_exc


# ---------------------------------------------------------------------------
# Cross-process advisory locking
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Backup pruning + Windows long-path handling + safe copy
# ---------------------------------------------------------------------------


def prune_old_backups(backup_parent: Path, *, keep_last: int = 20) -> list[Path]:
    """Remove oldest subdirs from a backup parent once count exceeds ``keep_last``.

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


def long_path(path: "os.PathLike[str] | str") -> str:
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
    shutil.copy2(long_path(src), long_path(dst))


# ---------------------------------------------------------------------------
# Path containment + traversal helpers
# ---------------------------------------------------------------------------


def ensure_path_within_dir(target_path: Path, base_dir: Path, label: str) -> None:
    """Raise ``PathEscapeError`` if ``target_path`` is not under ``base_dir``.

    On case-insensitive filesystems (Windows NTFS, macOS APFS-default) the
    raw string compare would mark ``C:\\Users\\Foo`` and ``c:\\users\\foo`` as
    distinct, falsely flagging legitimate paths as "escapes". ``normcase`` is a
    no-op on Linux so POSIX behaviour is unchanged.
    """
    try:
        target_real = os.path.realpath(target_path)
        base_real = os.path.realpath(base_dir)
        common = os.path.commonpath([target_real, base_real])
    except ValueError:
        common = ""

    if os.path.normcase(common) == os.path.normcase(base_real):
        return

    raise PathEscapeError(f"{label} escapes base directory: {target_path}")


def nearest_existing_parent(path_str: str) -> str:
    """Walk ``path_str`` upward and return the first ancestor that exists.

    Returns "" when the path is empty or no ancestor is found (e.g. cross-volume
    target on Windows). Useful when registering a workspace whose exact directory
    has been deleted but a parent still exists.
    """
    if not path_str:
        return ""
    path = Path(path_str).expanduser()
    while True:
        if path.exists():
            return str(path)
        if path.parent == path:
            return ""
        path = path.parent


__all__ = [
    "PathEscapeError",
    "atomic_write",
    "ensure_path_within_dir",
    "file_lock",
    "lock_path_for",
    "long_path",
    "nearest_existing_parent",
    "prune_old_backups",
    "replace_with_retry",
    "safe_copy2",
]
