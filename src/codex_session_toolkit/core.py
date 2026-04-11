"""Compatibility facade for the refactored package layout."""

from __future__ import annotations

from .commands import create_parser, main, run_cli
from .errors import ToolkitError
from .models import (
    BatchExportResult,
    BatchImportResult,
    BundleSummary,
    BundleValidationResult,
    CleanupResult,
    CloneFileResult,
    CloneRunResult,
    ExportResult,
    ImportResult,
    RepairResult,
    SessionSummary,
    ValidationReport,
)
from .paths import CodexPaths
from .presenters.reports import (
    print_batch_export_result,
    print_batch_import_result,
    print_bundle_rows,
    print_cleanup_result,
    print_clone_file_result,
    print_clone_run_result,
    print_export_result,
    print_import_result,
    print_repair_result,
    print_session_rows,
    print_validation_report,
)
from .services.browse import get_bundle_summaries, get_session_summaries, validate_bundles
from .services.clone import build_clone_index, cleanup_clones, clone_session_file, clone_to_provider
from .services.exporting import export_active_desktop_all, export_cli_all, export_desktop_all, export_session
from .services.importing import import_desktop_all, import_session
from .services.provider import detect_provider
from .services.repair import repair_desktop
from .stores.bundles import (
    bundle_directory_sort_key,
    collect_bundle_summaries,
    collect_known_bundle_summaries,
    iter_bundle_directories_under_root,
    iter_known_bundle_directories,
    resolve_bundle_dir,
    validate_bundle_directory,
)
from .stores.desktop_state import ensure_desktop_workspace_root, prepare_session_for_import, upsert_threads_table
from .stores.history import collect_history_lines_for_session, first_history_messages, first_history_text
from .stores.index import load_existing_index, salvage_index_line, upsert_session_index
from .stores.session_files import (
    collect_session_ids_for_kind,
    collect_session_summaries,
    extract_last_timestamp,
    extract_session_field_from_file,
    extract_timestamp_from_rollout_name,
    find_session_file,
    iter_session_files,
    parse_jsonl_records,
    read_session_payload,
    session_id_from_filename,
)
from .support import (
    backup_file,
    build_batch_export_root,
    build_single_export_root,
    classify_session_kind,
    export_batch_slug,
    extract_iso_timestamp,
    iso_to_epoch,
    nearest_existing_parent,
    normalize_bundle_root,
    normalize_iso,
    restrict_to_local_bundle_workspace,
)
from .validation import (
    ensure_path_within_dir,
    load_manifest,
    normalize_updated_at,
    validate_jsonl_file,
    validate_relative_path,
    validate_session_id,
)


def list_sessions(paths: CodexPaths, *, pattern: str = "", limit: int = 30) -> int:
    return print_session_rows(get_session_summaries(paths, pattern=pattern, limit=max(1, limit)))


def list_bundles(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: int = 30,
    source_group: str = "all",
) -> int:
    return print_bundle_rows(
        get_bundle_summaries(
            paths,
            pattern=pattern,
            limit=max(1, limit),
            source_group=source_group,
        )
    )


__all__ = [
    "BatchExportResult",
    "BatchImportResult",
    "BundleSummary",
    "BundleValidationResult",
    "CleanupResult",
    "CloneFileResult",
    "CloneRunResult",
    "CodexPaths",
    "ExportResult",
    "ImportResult",
    "RepairResult",
    "SessionSummary",
    "ToolkitError",
    "ValidationReport",
    "backup_file",
    "build_batch_export_root",
    "build_clone_index",
    "build_single_export_root",
    "bundle_directory_sort_key",
    "classify_session_kind",
    "cleanup_clones",
    "clone_session_file",
    "clone_to_provider",
    "collect_bundle_summaries",
    "collect_history_lines_for_session",
    "collect_known_bundle_summaries",
    "collect_session_ids_for_kind",
    "collect_session_summaries",
    "create_parser",
    "detect_provider",
    "ensure_desktop_workspace_root",
    "ensure_path_within_dir",
    "export_active_desktop_all",
    "export_batch_slug",
    "export_cli_all",
    "export_desktop_all",
    "export_session",
    "extract_iso_timestamp",
    "extract_last_timestamp",
    "extract_session_field_from_file",
    "extract_timestamp_from_rollout_name",
    "find_session_file",
    "first_history_messages",
    "first_history_text",
    "get_bundle_summaries",
    "get_session_summaries",
    "import_desktop_all",
    "import_session",
    "iso_to_epoch",
    "iter_bundle_directories_under_root",
    "iter_known_bundle_directories",
    "iter_session_files",
    "list_bundles",
    "list_sessions",
    "load_existing_index",
    "load_manifest",
    "main",
    "nearest_existing_parent",
    "normalize_bundle_root",
    "normalize_iso",
    "normalize_updated_at",
    "parse_jsonl_records",
    "prepare_session_for_import",
    "print_batch_export_result",
    "print_batch_import_result",
    "print_bundle_rows",
    "print_cleanup_result",
    "print_clone_file_result",
    "print_clone_run_result",
    "print_export_result",
    "print_import_result",
    "print_repair_result",
    "print_session_rows",
    "print_validation_report",
    "read_session_payload",
    "repair_desktop",
    "resolve_bundle_dir",
    "restrict_to_local_bundle_workspace",
    "run_cli",
    "salvage_index_line",
    "session_id_from_filename",
    "upsert_session_index",
    "upsert_threads_table",
    "validate_bundle_directory",
    "validate_bundles",
    "validate_jsonl_file",
    "validate_relative_path",
    "validate_session_id",
]
