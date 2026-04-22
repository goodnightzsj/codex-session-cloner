"""Shared data models and structured operation results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    scope: str
    path: Path
    preview: str
    kind: str
    cwd: str
    model_provider: str


@dataclass(frozen=True)
class BundleSummary:
    source_group: str
    session_id: str
    bundle_dir: Path
    relative_path: str
    updated_at: str
    exported_at: str
    thread_name: str
    session_cwd: str
    session_kind: str
    source_machine: str = ""
    source_machine_key: str = ""
    export_group: str = ""
    export_group_label: str = ""


@dataclass(frozen=True)
class BundleValidationResult:
    source_group: str
    bundle_dir: Path
    session_id: str
    is_valid: bool
    message: str


@dataclass(frozen=True)
class CloneFileResult:
    action: str
    message: str
    new_file_path: Optional[Path] = None


@dataclass(frozen=True)
class CloneRunResult:
    provider: str
    dry_run: bool
    stats: Dict[str, int]
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CleanupResult:
    provider: str
    dry_run: bool
    files_checked: int
    files_to_delete: List[Path]
    deleted: List[Path] = field(default_factory=list)
    errors: List[Tuple[Path, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DedupeResult:
    provider: str
    dry_run: bool
    files_checked: int
    duplicate_pairs: List[Tuple[Path, Path, str]]
    deleted_session_ids: List[str] = field(default_factory=list)
    deleted_files: List[Path] = field(default_factory=list)
    backup_root: Optional[Path] = None
    errors: List[Tuple[Path, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationReport:
    source_group: str
    results: List[BundleValidationResult]

    @property
    def valid_results(self) -> List[BundleValidationResult]:
        return [result for result in self.results if result.is_valid]

    @property
    def invalid_results(self) -> List[BundleValidationResult]:
        return [result for result in self.results if not result.is_valid]


@dataclass(frozen=True)
class ExportResult:
    session_id: str
    bundle_dir: Path
    relative_path: str
    session_kind: str
    session_cwd: str
    source_machine: str = ""
    source_machine_key: str = ""


@dataclass(frozen=True)
class BatchExportResult:
    summary_label: str
    bundle_root: Path
    export_root: Path
    machine_root: Path
    source_machine: str
    source_machine_key: str
    dry_run: bool
    active_only: bool
    session_kind: str
    session_ids: List[str]
    success_ids: List[str]
    failed_exports: List[Tuple[str, str]]
    manifest_file: Optional[Path] = None


@dataclass(frozen=True)
class ImportResult:
    session_id: str
    bundle_dir: Path
    relative_path: str
    import_mode: str
    rollout_action: str
    session_kind: str
    session_cwd: str
    desktop_registered: bool
    desktop_registration_target: str
    thread_row_upserted: bool
    target_desktop_model_provider: str
    resolved_from_session_id: bool = False
    created_workspace_dir: bool = False
    backup_path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)
    _index_entry: Optional[tuple] = None


@dataclass(frozen=True)
class BatchImportResult:
    bundle_root: Path
    desktop_visible: bool
    bundle_dirs: List[Path]
    success_dirs: List[Path]
    failed_imports: List[Tuple[Path, str]]
    machine_filter: str = ""
    machine_label: str = ""
    export_group_filter: str = ""
    export_group_label: str = ""
    latest_only: bool = False


@dataclass(frozen=True)
class RepairResult:
    provider: str
    dry_run: bool
    include_cli: bool
    entries_scanned: int
    desktop_retagged: int
    cli_converted: int
    skipped_sessions: List[str]
    workspace_roots_count: int
    threads_updated: int
    backup_root: Optional[Path]
    changed_sessions: List[str]
    warnings: List[str]
