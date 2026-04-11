"""Clone and cleanup services."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import CleanupResult, CloneFileResult, CloneRunResult
from ..paths import CodexPaths
from ..services.provider import detect_provider
from ..stores.session_files import (
    extract_timestamp_from_rollout_name,
    iter_session_files,
    parse_jsonl_records,
    read_session_payload,
)


def build_clone_index(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    active_only: bool = True,
    quiet: bool = False,
) -> set[str]:
    provider = detect_provider(paths, explicit=target_provider)
    cloned_from_ids: set[str] = set()
    total_files = 0

    if not quiet:
        print("Building clone index...", end="", flush=True)

    for session_file in iter_session_files(paths, active_only=active_only):
        total_files += 1
        try:
            payload = read_session_payload(session_file)
        except Exception:
            continue

        if payload.get("model_provider") != provider:
            continue

        origin_id = payload.get("cloned_from")
        if isinstance(origin_id, str) and origin_id:
            cloned_from_ids.add(origin_id)

    if not quiet:
        print(f" Done. Found {len(cloned_from_ids)} existing clones out of {total_files} files.")

    return cloned_from_ids


def clone_session_file(
    paths: CodexPaths,
    session_file: Path,
    *,
    target_provider: str = "",
    already_cloned_ids: Optional[set[str]] = None,
    dry_run: bool = False,
) -> CloneFileResult:
    session_file = Path(session_file).expanduser()
    provider = detect_provider(paths, explicit=target_provider)
    if already_cloned_ids is None:
        already_cloned_ids = build_clone_index(paths, target_provider=provider, quiet=True)

    try:
        records = parse_jsonl_records(session_file)
    except Exception as exc:
        return CloneFileResult("error", str(exc))

    if not records:
        return CloneFileResult("error", "Empty file")

    meta_index = -1
    session_meta: dict = {}
    for idx, (_, obj) in enumerate(records):
        if obj and obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
            meta_index = idx
            session_meta = dict(obj)
            break

    if meta_index < 0:
        return CloneFileResult("error", "Not a session file")

    payload = dict(session_meta["payload"])
    current_provider = payload.get("model_provider", "")
    current_id = payload.get("id")

    if not isinstance(current_id, str) or not current_id:
        return CloneFileResult("error", "Session id missing from session_meta")

    if current_provider == provider:
        return CloneFileResult("skipped_target", "Already on target provider")

    if current_id in already_cloned_ids:
        return CloneFileResult("skipped_exists", f"Already cloned (ID: {current_id})")

    new_id = str(uuid.uuid4())
    new_payload = dict(payload)
    new_payload["id"] = new_id
    new_payload["model_provider"] = provider
    new_payload["cloned_from"] = current_id
    new_payload["original_provider"] = current_provider
    new_payload["clone_timestamp"] = datetime.now(timezone.utc).isoformat()
    session_meta["payload"] = new_payload

    old_filename = session_file.name
    if current_id in old_filename:
        new_filename = old_filename.replace(current_id, new_id, 1)
    else:
        timestamp = extract_timestamp_from_rollout_name(old_filename)
        new_filename = f"rollout-{timestamp}-{new_id}.jsonl" if timestamp else f"rollout-CLONE-{new_id}.jsonl"

    new_file_path = session_file.with_name(new_filename)
    if new_file_path.exists():
        return CloneFileResult("skipped_exists", "Target file collision")

    output_lines = []
    for idx, (raw, _) in enumerate(records):
        if idx == meta_index:
            output_lines.append(json.dumps(session_meta, ensure_ascii=False, separators=(",", ":")) + "\n")
        else:
            output_lines.append(raw)

    if not dry_run:
        with new_file_path.open("w", encoding="utf-8") as fh:
            fh.writelines(output_lines)

    already_cloned_ids.add(current_id)
    action_prefix = "[DRY-RUN] Would create" if dry_run else "Created"
    message = f"{action_prefix} {new_filename} (from {current_provider or 'unknown'})"
    return CloneFileResult("cloned", message, new_file_path)


def clone_to_provider(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = True,
) -> CloneRunResult:
    provider = detect_provider(paths, explicit=target_provider)
    already_cloned = build_clone_index(paths, target_provider=provider, active_only=active_only)
    stats = {
        "cloned": 0,
        "skipped_exists": 0,
        "skipped_target": 0,
        "error": 0,
    }
    messages = []
    errors = []

    for session_file in iter_session_files(paths, active_only=active_only):
        result = clone_session_file(
            paths,
            session_file,
            target_provider=provider,
            already_cloned_ids=already_cloned,
            dry_run=dry_run,
        )
        stats[result.action] = stats.get(result.action, 0) + 1
        if result.action == "cloned":
            messages.append(result.message)
        elif result.action == "error":
            errors.append(f"{session_file.name}: {result.message}")

    return CloneRunResult(
        provider=provider,
        dry_run=dry_run,
        stats=stats,
        messages=messages,
        errors=errors,
    )


def cleanup_clones(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = True,
) -> CleanupResult:
    provider = detect_provider(paths, explicit=target_provider)

    originals_by_ts: set[str] = set()
    targets_without_tag_by_ts: dict[str, list[Path]] = {}
    files_checked = 0

    for session_file in iter_session_files(paths, active_only=active_only):
        files_checked += 1
        timestamp = extract_timestamp_from_rollout_name(session_file.name)
        if not timestamp:
            continue

        try:
            payload = read_session_payload(session_file)
        except Exception:
            continue

        current_provider = payload.get("model_provider", "")
        cloned_from = payload.get("cloned_from")
        if current_provider == provider:
            if not isinstance(cloned_from, str) or not cloned_from:
                targets_without_tag_by_ts.setdefault(timestamp, []).append(session_file)
        else:
            originals_by_ts.add(timestamp)

    files_to_delete: list[Path] = []
    for timestamp, paths_for_ts in targets_without_tag_by_ts.items():
        if timestamp in originals_by_ts:
            files_to_delete.extend(paths_for_ts)

    deleted = []
    errors = []
    if not dry_run:
        for target_path in files_to_delete:
            try:
                target_path.unlink()
                deleted.append(target_path)
            except Exception as exc:
                errors.append((target_path, str(exc)))

    return CleanupResult(
        provider=provider,
        dry_run=dry_run,
        files_checked=files_checked,
        files_to_delete=files_to_delete,
        deleted=deleted,
        errors=errors,
    )
