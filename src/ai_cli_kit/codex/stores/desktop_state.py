"""Desktop state and SQLite helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from ..errors import ToolkitError
from ..stores.history import first_history_messages
from ..stores.session_files import build_session_preview, is_placeholder_thread_name
from ..support import atomic_write, file_lock, iso_to_epoch, lock_path_for, long_path


def _is_subpath(child: Path, parent: Path) -> bool:
    """Case-insensitive subpath check (Win NTFS / macOS APFS-default).

    On case-insensitive filesystems, ``Path.relative_to`` does a literal string
    compare and would treat case-variant roots (``C:\\Users\\Foo`` vs
    ``c:\\users\\foo``) as distinct. ``os.path.normcase`` normalises that
    difference; on Linux it is the identity function so POSIX behavior is
    unchanged.
    """
    try:
        child_norm = Path(os.path.normcase(str(child)))
        parent_norm = Path(os.path.normcase(str(parent)))
        child_norm.relative_to(parent_norm)
        return True
    except ValueError:
        return False


def _paths_equal_ci(a: Path, b: Path) -> bool:
    """Case-insensitive path equality (matches ``_is_subpath`` semantics)."""
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def ensure_desktop_workspace_root(workspace_dir: str, state_file: Path) -> bool:
    if not state_file.exists():
        print(f"Warning: Codex Desktop state file not found: {state_file}", file=sys.stderr)
        return False

    # Hold the canonical state.json lock for the full read-modify-write so concurrent
    # repair_desktop() (which also rewrites state.json) cannot clobber our addition,
    # and vice versa. lock_path_for() ensures we share the same lock-file path.
    with file_lock(lock_path_for(state_file)):
        with state_file.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        saved = list(data.setdefault("electron-saved-workspace-roots", []))
        project_order = list(data.setdefault("project-order", []))

        covered = False
        workspace_path = Path(workspace_dir)
        for root in saved:
            existing = Path(root)
            if _paths_equal_ci(workspace_path, existing) or _is_subpath(workspace_path, existing):
                covered = True
                break

        if not covered:
            saved.append(workspace_dir)
            normcased_order = {os.path.normcase(item) for item in project_order}
            if os.path.normcase(workspace_dir) not in normcased_order:
                project_order.append(workspace_dir)

        data["electron-saved-workspace-roots"] = saved
        data["active-workspace-roots"] = list(saved)
        data["project-order"] = project_order

        with atomic_write(state_file) as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")
    return True


def prepare_session_for_import(
    source_session: Path,
    prepared_session: Path,
    *,
    auto_desktop_compat: bool,
    session_kind: str,
    target_desktop_model_provider: str,
) -> None:
    # newline="" preserves LF line endings across platforms — critical on Windows
    # where text-mode write would translate \n → \r\n and break byte-comparison
    # with the existing target_session in importing.py (read_bytes-based diff).
    with source_session.open("r", encoding="utf-8", newline="") as in_fh, \
            prepared_session.open("w", encoding="utf-8", newline="") as out_fh:
        for raw in in_fh:
            line = raw.rstrip("\n")
            if not line:
                out_fh.write(raw)
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                out_fh.write(raw)
                continue

            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                payload = dict(obj["payload"])
                if auto_desktop_compat and session_kind == "cli":
                    payload["source"] = "vscode"
                    payload["originator"] = "Codex Desktop"
                if target_desktop_model_provider:
                    payload["model_provider"] = target_desktop_model_provider

                obj = dict(obj)
                obj["payload"] = payload
                out_fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
                continue

            out_fh.write(raw)


def upsert_threads_table(
    state_db: Path,
    session_file: Path,
    history_file: Path,
    target_rollout: Path,
    *,
    session_id: str,
    thread_name: str,
    updated_at: str,
    session_cwd: str,
    session_source: str,
    session_originator: str,
    session_kind: str,
    classify_session_kind,
) -> bool:
    if not state_db or not state_db.is_file():
        return False

    meta: dict = {}
    turn_context: dict = {}
    last_timestamp = ""

    with session_file.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception as exc:
                raise ToolkitError(f"Failed to parse prepared session file at line {line_number}: {exc}") from exc
            last_timestamp = obj.get("timestamp", last_timestamp)
            if obj.get("type") == "session_meta":
                meta = obj.get("payload", {})
            elif obj.get("type") == "turn_context" and not turn_context:
                turn_context = obj.get("payload", {})

    history_preview = first_history_messages(history_file).get(session_id, "")

    source_name = session_source or meta.get("source", "")
    originator_name = session_originator or meta.get("originator", "")
    effective_kind = session_kind or classify_session_kind(source_name, originator_name)
    cwd = session_cwd or meta.get("cwd", "")
    first_user_message = build_session_preview(history_preview, session_file, cwd)
    created_iso = meta.get("timestamp") or last_timestamp or updated_at
    updated_iso = updated_at or last_timestamp or created_iso
    title = (
        first_user_message
        if is_placeholder_thread_name(thread_name, session_id)
        else thread_name or first_user_message or session_id
    )
    sandbox_policy = json.dumps(turn_context.get("sandbox_policy", {}), ensure_ascii=False, separators=(",", ":"))
    approval_mode = turn_context.get("approval_policy", "on-request")
    model_provider = meta.get("model_provider", "")
    cli_version = meta.get("cli_version", "")
    model = turn_context.get("model")
    reasoning_effort = turn_context.get("effort")
    archived = 1 if "archived_sessions" in target_rollout.parts else 0
    archived_at = iso_to_epoch(updated_iso) if archived else None

    # long_path() prefixes \\?\ on Windows so sqlite3 (CreateFileW under the
    # hood) can open paths > MAX_PATH. POSIX no-op.
    with sqlite3.connect(long_path(state_db), timeout=30) as conn:
        cur = conn.cursor()
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if not row:
            return False

        columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
        data = {
            "id": session_id,
            "rollout_path": str(target_rollout),
            "created_at": iso_to_epoch(created_iso),
            "updated_at": iso_to_epoch(updated_iso),
            "source": source_name or ("vscode" if effective_kind == "desktop" else "cli" if effective_kind == "cli" else "unknown"),
            "model_provider": model_provider,
            "cwd": cwd,
            "title": title,
            "sandbox_policy": sandbox_policy,
            "approval_mode": approval_mode,
            "tokens_used": 0,
            "has_user_event": 1,
            "archived": archived,
            "archived_at": archived_at,
            "cli_version": cli_version,
            "first_user_message": first_user_message or title,
            "memory_mode": "enabled",
            "model": model,
            "reasoning_effort": reasoning_effort,
        }

        insert_cols = [c for c in data if c in columns]
        placeholders = ", ".join("?" for _ in insert_cols)
        col_list = ", ".join(insert_cols)
        update_cols = [c for c in insert_cols if c != "id"]
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        values = [data[c] for c in insert_cols]

        sql = f"insert into threads ({col_list}) values ({placeholders}) on conflict(id) do update set {update_sql}"
        cur.execute(sql, values)
        conn.commit()
    return True
