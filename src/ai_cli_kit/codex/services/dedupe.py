"""Session deduplication services."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import DedupeResult
from ..paths import CodexPaths
from ..services.provider import detect_provider
from ..stores.index import remove_session_index_entries
from ..stores.session_files import iter_session_files, read_session_payload
from ..support import backup_file, long_path, prune_old_backups


def _is_archived_session(path: Path) -> bool:
    return "archived_sessions" in path.parts


def _select_delete_target(
    original_path: Path,
    clone_path: Path,
) -> tuple[Path, Path, str]:
    original_archived = _is_archived_session(original_path)
    clone_archived = _is_archived_session(clone_path)

    if original_archived and not clone_archived:
        return original_path, clone_path, "keep_active_clone"
    if clone_archived and not original_archived:
        return clone_path, original_path, "keep_active_original"
    return clone_path, original_path, "keep_original"


def _prune_state_file(state_file: Path, deleted_session_ids: set[str]) -> None:
    if not deleted_session_ids or not state_file.exists():
        return

    data = json.loads(state_file.read_text(encoding="utf-8"))

    def prune_mapping(mapping: object) -> object:
        if not isinstance(mapping, dict):
            return mapping
        return {key: value for key, value in mapping.items() if key not in deleted_session_ids}

    data["thread-workspace-root-hints"] = prune_mapping(data.get("thread-workspace-root-hints"))
    atom_state = data.get("electron-persisted-atom-state", {})
    if isinstance(atom_state, dict):
        atom_state["thread-workspace-root-hints"] = prune_mapping(atom_state.get("thread-workspace-root-hints"))
        thread_titles = atom_state.get("thread-titles")
        if isinstance(thread_titles, dict):
            thread_titles["titles"] = prune_mapping(thread_titles.get("titles"))

    state_file.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _delete_threads_rows(state_db: Path | None, deleted_session_ids: set[str]) -> None:
    if not deleted_session_ids or state_db is None or not state_db.exists():
        return

    # long_path() prefixes \\?\ on Windows for paths > MAX_PATH so sqlite3
    # (which uses CreateFileW under the hood) can open them. POSIX no-op.
    with sqlite3.connect(long_path(state_db), timeout=30) as conn:
        cur = conn.cursor()
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if row:
            cur.executemany("delete from threads where id = ?", [(session_id,) for session_id in deleted_session_ids])

        edge_row = cur.execute("select name from sqlite_master where type='table' and name='thread_spawn_edges'").fetchone()
        if edge_row:
            columns = [info[1] for info in cur.execute("pragma table_info(thread_spawn_edges)").fetchall()]
            if "parent_thread_id" in columns:
                cur.executemany(
                    "delete from thread_spawn_edges where parent_thread_id = ?",
                    [(session_id,) for session_id in deleted_session_ids],
                )
            if "child_thread_id" in columns:
                cur.executemany(
                    "delete from thread_spawn_edges where child_thread_id = ?",
                    [(session_id,) for session_id in deleted_session_ids],
                )

        conn.commit()


def dedupe_clones(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = False,
) -> DedupeResult:
    provider = detect_provider(paths, explicit=target_provider)
    files_checked = 0
    sessions_by_id: dict[str, tuple[Path, dict]] = {}

    for session_file in iter_session_files(paths, active_only=active_only):
        files_checked += 1
        try:
            payload = read_session_payload(session_file)
        except Exception:
            continue

        session_id = payload.get("id")
        if not isinstance(session_id, str) or not session_id:
            continue
        sessions_by_id[session_id] = (session_file, payload)

    # Build the reverse-edge set: which session ids appear as someone else's `cloned_from`?
    # If B is A's clone AND C is B's clone, B is a chain intermediate — deleting B would
    # orphan C's lineage. Skip any candidate that is still referenced by a downstream clone.
    referenced_as_origin: set[str] = set()
    for _, payload in sessions_by_id.values():
        origin = payload.get("cloned_from")
        if isinstance(origin, str) and origin:
            referenced_as_origin.add(origin)

    duplicate_pairs: list[tuple[Path, Path, str]] = []
    seen_delete_paths: set[str] = set()

    for clone_session_id, (clone_path, clone_payload) in sessions_by_id.items():
        if clone_payload.get("model_provider") != provider:
            continue

        cloned_from = clone_payload.get("cloned_from")
        if not isinstance(cloned_from, str) or not cloned_from:
            continue

        original = sessions_by_id.get(cloned_from)
        if original is None:
            continue

        # Skip chain intermediates: this clone is also an origin for another clone.
        if clone_session_id in referenced_as_origin:
            continue

        original_path, _ = original
        delete_path, keep_path, reason = _select_delete_target(original_path, clone_path)
        delete_key = str(delete_path.resolve())
        if delete_key in seen_delete_paths:
            continue
        seen_delete_paths.add(delete_key)
        duplicate_pairs.append((delete_path, keep_path, reason))

    if dry_run or not duplicate_pairs:
        return DedupeResult(
            provider=provider,
            dry_run=dry_run,
            files_checked=files_checked,
            duplicate_pairs=duplicate_pairs,
        )

    backup_parent = paths.code_dir / "repair_backups"
    prune_old_backups(backup_parent, keep_last=20)
    backup_root = backup_parent / f"dedupe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    backed_up: set[str] = set()
    deleted_session_ids: list[str] = []
    deleted_files: list[Path] = []
    errors: list[tuple[Path, str]] = []

    for delete_path, _, _ in duplicate_pairs:
        try:
            payload = read_session_payload(delete_path)
            session_id = payload.get("id")
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("session_meta payload.id is missing")
            backup_file(paths.code_dir, backup_root, backed_up, delete_path, enabled=True)
            delete_path.unlink()
            deleted_session_ids.append(session_id)
            deleted_files.append(delete_path)
        except Exception as exc:
            errors.append((delete_path, str(exc)))

    deleted_session_id_set = set(deleted_session_ids)
    if deleted_session_id_set:
        remove_session_index_entries(paths.index_file, deleted_session_id_set)
        _delete_threads_rows(paths.latest_state_db(), deleted_session_id_set)
        _prune_state_file(paths.state_file, deleted_session_id_set)

    return DedupeResult(
        provider=provider,
        dry_run=False,
        files_checked=files_checked,
        duplicate_pairs=duplicate_pairs,
        deleted_session_ids=deleted_session_ids,
        deleted_files=deleted_files,
        backup_root=backup_root,
        errors=errors,
    )
