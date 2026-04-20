"""Bundle export services."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..errors import ToolkitError
from ..models import BatchExportResult, ExportResult
from ..paths import CodexPaths
from ..stores.history import collect_history_lines_for_session, first_history_text
from ..stores.session_files import (
    collect_session_ids_for_kind,
    extract_last_timestamp,
    extract_session_meta_fields,
    find_session_file,
)
from ..support import (
    atomic_write,
    build_batch_export_root,
    build_machine_bundle_root,
    build_single_export_root,
    classify_session_kind,
    detect_machine_key,
    detect_machine_label,
    normalize_bundle_root,
    safe_copy2,
)
from ..validation import validate_jsonl_file, validate_session_id


def export_session(
    paths: CodexPaths,
    session_id: str,
    *,
    bundle_root: Optional[Path] = None,
) -> ExportResult:
    session_id = validate_session_id(session_id)
    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    if bundle_root is None:
        base_bundle_root = normalize_bundle_root(paths, None, paths.default_bundle_root)
        bundle_root = build_single_export_root(base_bundle_root, machine_key)
    else:
        bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    session_file = find_session_file(paths, session_id)
    if not session_file:
        raise ToolkitError(f"Session not found: {session_id}")

    try:
        relative_path = session_file.relative_to(paths.code_dir)
    except ValueError as exc:
        raise ToolkitError(f"Unexpected session path: {session_file}") from exc

    final_bundle_dir = bundle_root / session_id
    stage_root = Path(tempfile.mkdtemp(prefix=".cst-exp-", dir=str(bundle_root)))
    stage_bundle_dir = stage_root / session_id
    old_bundle_backup: Optional[Path] = None

    try:
        bundle_codex_dir = stage_bundle_dir / "codex"
        bundle_history = stage_bundle_dir / "history.jsonl"
        manifest_file = stage_bundle_dir / "manifest.env"

        (bundle_codex_dir / relative_path.parent).mkdir(parents=True, exist_ok=True)

        bundled_session = bundle_codex_dir / relative_path
        safe_copy2(session_file, bundled_session)
        validate_jsonl_file(bundled_session, "Bundled session file", "session", session_id)

        history_lines = collect_history_lines_for_session(paths.history_file, session_id)
        bundle_history.parent.mkdir(parents=True, exist_ok=True)
        with bundle_history.open("w", encoding="utf-8") as fh:
            fh.writelines(history_lines)
        validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

        first_prompt = first_history_text(history_lines)
        meta_fields = extract_session_meta_fields(session_file, "cwd", "source", "originator")
        session_cwd = meta_fields["cwd"]
        session_source = meta_fields["source"]
        session_originator = meta_fields["originator"]
        session_kind = classify_session_kind(session_source, session_originator)
        last_updated = extract_last_timestamp(session_file) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        manifest_data = OrderedDict(
            SESSION_ID=session_id,
            RELATIVE_PATH=relative_path.as_posix(),
            EXPORTED_AT=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            UPDATED_AT=last_updated,
            THREAD_NAME=first_prompt[:80],
            SESSION_CWD=session_cwd,
            SESSION_SOURCE=session_source,
            SESSION_ORIGINATOR=session_originator,
            SESSION_KIND=session_kind,
            EXPORT_MACHINE=machine_label,
            EXPORT_MACHINE_KEY=machine_key,
        )
        with manifest_file.open("w", encoding="utf-8") as fh:
            for key, value in manifest_data.items():
                fh.write(f"{key}={shlex.quote(value)}\n")

        if final_bundle_dir.exists():
            old_bundle_backup = bundle_root / f".{session_id}.bak.{int(datetime.now().timestamp())}"
            final_bundle_dir.rename(old_bundle_backup)

        stage_bundle_dir.rename(final_bundle_dir)
        shutil.rmtree(stage_root, ignore_errors=True)

        if old_bundle_backup and old_bundle_backup.exists():
            shutil.rmtree(old_bundle_backup, ignore_errors=True)

        return ExportResult(
            session_id=session_id,
            bundle_dir=final_bundle_dir,
            relative_path=relative_path.as_posix(),
            session_kind=session_kind,
            session_cwd=session_cwd,
            source_machine=machine_label,
            source_machine_key=machine_key,
        )
    except Exception:
        if old_bundle_backup and old_bundle_backup.exists() and not final_bundle_dir.exists():
            old_bundle_backup.rename(final_bundle_dir)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def export_sessions_for_kind(
    paths: CodexPaths,
    *,
    session_kind: str,
    bundle_root: Path,
    dry_run: bool,
    active_only: bool,
    manifest_stem: str,
    summary_label: str,
    archive_group: str,
) -> BatchExportResult:
    session_ids = collect_session_ids_for_kind(paths, session_kind=session_kind, active_only=active_only)
    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    machine_root = build_machine_bundle_root(bundle_root, machine_key)
    export_root = build_batch_export_root(bundle_root, archive_group, machine_key)

    if dry_run:
        return BatchExportResult(
            summary_label=summary_label,
            bundle_root=bundle_root,
            export_root=export_root,
            machine_root=machine_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            dry_run=True,
            active_only=active_only,
            session_kind=session_kind,
            session_ids=session_ids,
            success_ids=[],
            failed_exports=[],
            manifest_file=None,
        )

    if not session_ids:
        return BatchExportResult(
            summary_label=summary_label,
            bundle_root=bundle_root,
            export_root=export_root,
            machine_root=machine_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            dry_run=False,
            active_only=active_only,
            session_kind=session_kind,
            session_ids=[],
            success_ids=[],
            failed_exports=[],
            manifest_file=None,
        )

    export_root.mkdir(parents=True, exist_ok=True)
    success_ids: list[str] = []
    failed_exports: list[tuple[str, str]] = []

    total = len(session_ids)
    for i, session_id in enumerate(session_ids, 1):
        print(f"[{i}/{total}] exporting {session_id}...", flush=True)
        try:
            export_session(paths, session_id, bundle_root=export_root)
            success_ids.append(session_id)
        except Exception as exc:
            print(f"[{i}/{total}] FAILED {session_id}: {exc}", file=sys.stderr, flush=True)
            failed_exports.append((session_id, str(exc)))

    manifest_file = export_root / f"_{manifest_stem}_export_manifest.txt"
    with atomic_write(manifest_file) as fh:
        fh.write(f"# exported_at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        fh.write(f"# session_kind={session_kind}\n")
        fh.write(f"# active_only={1 if active_only else 0}\n")
        fh.write(f"# count={len(success_ids)}\n")
        for session_id in success_ids:
            fh.write(session_id + "\n")

    return BatchExportResult(
        summary_label=summary_label,
        bundle_root=bundle_root,
        export_root=export_root,
        machine_root=machine_root,
        source_machine=machine_label,
        source_machine_key=machine_key,
        dry_run=False,
        active_only=active_only,
        session_kind=session_kind,
        session_ids=session_ids,
        success_ids=success_ids,
        failed_exports=failed_exports,
        manifest_file=manifest_file,
    )


def export_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
    active_only: bool = False,
) -> BatchExportResult:
    return export_sessions_for_kind(
        paths,
        session_kind="desktop",
        bundle_root=normalize_bundle_root(paths, bundle_root, paths.default_bundle_root),
        dry_run=dry_run,
        active_only=active_only,
        manifest_stem=("active_desktop" if active_only else "desktop"),
        summary_label=("Active Desktop" if active_only else "Desktop"),
        archive_group=("active" if active_only else "desktop"),
    )


def export_active_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
) -> BatchExportResult:
    return export_desktop_all(paths, bundle_root=bundle_root, dry_run=dry_run, active_only=True)


def export_cli_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
) -> BatchExportResult:
    return export_sessions_for_kind(
        paths,
        session_kind="cli",
        bundle_root=normalize_bundle_root(paths, bundle_root, paths.default_bundle_root),
        dry_run=dry_run,
        active_only=False,
        manifest_stem="cli",
        summary_label="CLI",
        archive_group="cli",
    )
