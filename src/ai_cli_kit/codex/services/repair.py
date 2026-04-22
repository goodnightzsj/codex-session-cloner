"""Desktop repair service."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ToolkitError
from ..models import RepairResult
from ..paths import CodexPaths
from ..services.provider import detect_provider
from ..stores.history import first_history_messages
from ..stores.index import load_existing_index
from ..stores.session_files import (
    build_session_preview,
    is_placeholder_thread_name,
    iter_session_files,
    parse_jsonl_records,
)
from ..support import (
    atomic_write,
    backup_file,
    classify_session_kind,
    file_lock,
    iso_to_epoch,
    lock_path_for,
    long_path,
    nearest_existing_parent,
    normalize_iso,
    prune_old_backups,
)


def _string_field(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _sqlite_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def repair_desktop(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    include_cli: bool = False,
) -> RepairResult:
    if not paths.code_dir.is_dir():
        raise ToolkitError(f"Missing Codex data directory: {paths.code_dir}")

    provider = detect_provider(paths, explicit=target_provider)
    backup_parent = paths.code_dir / "repair_backups"
    if not dry_run:
        prune_old_backups(backup_parent, keep_last=20)
    backup_root = backup_parent / f"visibility-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    backed_up: set[str] = set()
    warnings: list[str] = []

    history_first_messages = first_history_messages(paths.history_file)
    existing_index = load_existing_index(paths.index_file)
    state_db = paths.latest_state_db()

    entries: list[dict] = []
    changed_sessions: list[str] = []
    skipped_sessions: list[str] = []
    workspace_candidates: "OrderedDict[str, bool]" = OrderedDict()
    desktop_retagged = 0
    cli_converted = 0

    for session_file in iter_session_files(paths):
        try:
            records = parse_jsonl_records(session_file)
        except ToolkitError as exc:
            warnings.append(f"Skipped invalid session file: {exc}")
            skipped_sessions.append(str(session_file))
            continue

        session_meta = None
        turn_context: dict = {}
        last_timestamp = ""

        for raw, obj in records:
            if not obj:
                continue
            timestamp = obj.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                last_timestamp = timestamp
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                session_meta = dict(obj["payload"])
            elif obj.get("type") == "turn_context" and not turn_context and isinstance(obj.get("payload"), dict):
                turn_context = dict(obj["payload"])

        if not session_meta:
            warnings.append(f"Skipped session without session_meta: {session_file}")
            skipped_sessions.append(str(session_file))
            continue

        session_id = session_meta.get("id")
        if not isinstance(session_id, str) or not session_id:
            warnings.append(f"Skipped session without payload.id: {session_file}")
            skipped_sessions.append(str(session_file))
            continue

        source_name = _string_field(session_meta.get("source"))
        originator_name = _string_field(session_meta.get("originator"))
        session_kind = classify_session_kind(source_name, originator_name)
        desktop_like = session_kind == "desktop"
        convert_cli = include_cli and session_kind == "cli"

        updated_meta = dict(session_meta)
        changed = False

        if desktop_like and provider and updated_meta.get("model_provider") != provider:
            updated_meta["model_provider"] = provider
            changed = True
            desktop_retagged += 1

        if convert_cli:
            if updated_meta.get("source") != "vscode":
                updated_meta["source"] = "vscode"
                changed = True
            if updated_meta.get("originator") != "Codex Desktop":
                updated_meta["originator"] = "Codex Desktop"
                changed = True
            if provider and updated_meta.get("model_provider") != provider:
                updated_meta["model_provider"] = provider
                changed = True
            if changed:
                cli_converted += 1
            source_name = updated_meta.get("source", source_name)
            originator_name = updated_meta.get("originator", originator_name)
            session_kind = "desktop"
            desktop_like = True

        if changed:
            changed_sessions.append(str(session_file))
            if not dry_run:
                backup_file(paths.code_dir, backup_root, backed_up, session_file, enabled=True)
                with atomic_write(session_file) as fh:
                    for raw, obj in records:
                        if not obj:
                            fh.write(raw)
                            continue
                        if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                            patched = dict(obj)
                            patched["payload"] = updated_meta
                            fh.write(json.dumps(patched, ensure_ascii=False, separators=(",", ":")) + "\n")
                        else:
                            fh.write(raw)

        session_meta = updated_meta
        created_iso = normalize_iso(str(session_meta.get("timestamp", ""))) or normalize_iso(last_timestamp)
        updated_iso = (
            normalize_iso(last_timestamp)
            or created_iso
            or existing_index.get(session_id, {}).get("updated_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        cwd = session_meta.get("cwd", "") if isinstance(session_meta.get("cwd", ""), str) else ""
        preview_title = build_session_preview(history_first_messages.get(session_id, ""), session_file, cwd)
        existing_thread_name = existing_index.get(session_id, {}).get("thread_name", "")
        thread_name = (
            preview_title
            if is_placeholder_thread_name(existing_thread_name, session_id)
            else existing_thread_name or preview_title or session_id
        )
        if cwd:
            candidate = nearest_existing_parent(cwd) or cwd
            if candidate and candidate not in workspace_candidates:
                workspace_candidates[candidate] = True

        entries.append(
            {
                "id": session_id,
                "thread_name": thread_name,
                "updated_at": updated_iso,
                "session_file": session_file,
                "source": source_name,
                "originator": originator_name,
                "kind": session_kind,
                "cwd": cwd,
                "created_iso": created_iso or updated_iso,
                "updated_iso": updated_iso,
                "first_user_message": preview_title or thread_name,
                "sandbox_policy": json.dumps(turn_context.get("sandbox_policy", {}), ensure_ascii=False, separators=(",", ":")),
                "approval_mode": turn_context.get("approval_policy", "on-request"),
                "model_provider": session_meta.get("model_provider", "") if isinstance(session_meta.get("model_provider", ""), str) else "",
                "cli_version": session_meta.get("cli_version", "") if isinstance(session_meta.get("cli_version", ""), str) else "",
                "model": turn_context.get("model"),
                "reasoning_effort": turn_context.get("effort"),
                "archived": 1 if "archived_sessions" in session_file.parts else 0,
            }
        )

    entries.sort(key=lambda item: (iso_to_epoch(item["updated_at"]), item["id"]), reverse=True)

    if not dry_run:
        backup_file(paths.code_dir, backup_root, backed_up, paths.index_file, enabled=True)
        # Serialise against concurrent upsert/remove on session_index.jsonl.
        with atomic_write(paths.index_file, lock_path=lock_path_for(paths.index_file)) as fh:
            for entry in entries:
                obj = {
                    "id": entry["id"],
                    "thread_name": entry["thread_name"],
                    "updated_at": entry["updated_at"],
                }
                fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    state_lock_path = lock_path_for(paths.state_file)
    # Hold the state.json lock for the entire read-modify-write so concurrent
    # ensure_desktop_workspace_root() (or other repair runs) cannot clobber
    # the workspace-roots merge we are about to compute.
    with file_lock(state_lock_path):
        try:
            state_data = json.loads(paths.state_file.read_text(encoding="utf-8")) if paths.state_file.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Warning: failed to read state file {paths.state_file}: {exc}")
            state_data = {}

        saved_roots = list(state_data.get("electron-saved-workspace-roots", []))
        project_order = list(state_data.get("project-order", []))

        # On case-insensitive filesystems (Windows NTFS, macOS APFS-default)
        # `relative_to` is a string compare and would treat `C:\Users\Foo`
        # vs `c:\users\foo` as distinct, allowing duplicate workspace entries
        # to accumulate. Compare under normcase so case-only variants dedupe.
        def _is_subpath_ci(child: Path, parent: Path) -> bool:
            try:
                child_norm = Path(os.path.normcase(str(child)))
                parent_norm = Path(os.path.normcase(str(parent)))
                child_norm.relative_to(parent_norm)
                return True
            except ValueError:
                return False

        normcased_existing = {os.path.normcase(item) for item in saved_roots}
        normcased_order = {os.path.normcase(item) for item in project_order}
        for root in workspace_candidates:
            root_path = Path(root)
            covered = any(
                _is_subpath_ci(root_path, Path(existing)) for existing in saved_roots
            )
            if not covered:
                saved_roots.append(root)
                normcased_existing.add(os.path.normcase(root))
                if os.path.normcase(root) not in normcased_order:
                    project_order.append(root)
                    normcased_order.add(os.path.normcase(root))

        if not dry_run:
            state_data["electron-saved-workspace-roots"] = saved_roots
            state_data["active-workspace-roots"] = list(saved_roots)
            state_data["project-order"] = project_order
        else:
            state_data = dict(state_data)
            state_data["active-workspace-roots"] = list(saved_roots)

        if not dry_run:
            backup_file(paths.code_dir, backup_root, backed_up, paths.state_file, enabled=True)
            with atomic_write(paths.state_file) as fh:
                json.dump(state_data, fh, ensure_ascii=False, separators=(",", ":"))
                fh.write("\n")

    threads_updated = 0
    if state_db and state_db.exists():
        if not dry_run:
            backup_file(paths.code_dir, backup_root, backed_up, state_db, enabled=True)
        # long_path() prefixes \\?\ on Windows when the path exceeds MAX_PATH
        # (260 chars); sqlite3 ultimately uses CreateFileW which honours that
        # prefix. No-op on POSIX where it just returns the original string.
        with sqlite3.connect(long_path(state_db), timeout=30) as conn:
            cur = conn.cursor()
            row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
            if row:
                columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
                updatable_entries = [entry for entry in entries if entry["kind"] == "desktop"]
                for entry in updatable_entries:
                    data = {
                        "id": entry["id"],
                        "rollout_path": str(entry["session_file"]),
                        "created_at": iso_to_epoch(entry["created_iso"]),
                        "updated_at": iso_to_epoch(entry["updated_iso"]),
                        "source": (entry["source"] if isinstance(entry["source"], str) and entry["source"] else "vscode"),
                        "model_provider": provider,
                        "cwd": entry["cwd"],
                        "title": entry["thread_name"],
                        "sandbox_policy": entry["sandbox_policy"],
                        "approval_mode": entry["approval_mode"],
                        "tokens_used": 0,
                        "has_user_event": 1,
                        "archived": entry["archived"],
                        "archived_at": iso_to_epoch(entry["updated_iso"]) if entry["archived"] else None,
                        "cli_version": entry["cli_version"],
                        "first_user_message": entry["first_user_message"],
                        "memory_mode": "enabled",
                        "model": entry["model"],
                        "reasoning_effort": entry["reasoning_effort"],
                    }
                    insert_cols = [name for name in data if name in columns]
                    placeholders = ", ".join("?" for _ in insert_cols)
                    col_list = ", ".join(insert_cols)
                    update_cols = [name for name in insert_cols if name != "id"]
                    update_sql = ", ".join(f"{name}=excluded.{name}" for name in update_cols)
                    values = [_sqlite_value(data[name]) for name in insert_cols]
                    sql = f"insert into threads ({col_list}) values ({placeholders}) on conflict(id) do update set {update_sql}"
                    if not dry_run:
                        cur.execute(sql, values)
                    threads_updated += 1

                if not dry_run:
                    conn.commit()
            else:
                warnings.append(f"threads table not found in {state_db}")

    return RepairResult(
        provider=provider,
        dry_run=dry_run,
        include_cli=include_cli,
        entries_scanned=len(entries),
        desktop_retagged=desktop_retagged,
        cli_converted=cli_converted,
        skipped_sessions=skipped_sessions,
        workspace_roots_count=len(state_data.get("active-workspace-roots", [])),
        threads_updated=threads_updated,
        backup_root=(None if dry_run else backup_root),
        changed_sessions=changed_sessions,
        warnings=warnings,
    )
