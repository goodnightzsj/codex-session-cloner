"""Bundle repository helpers."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from ..errors import ToolkitError
from ..models import BundleSummary, BundleValidationResult
from ..paths import CodexPaths
from ..support import ensure_path_within_dir, iso_to_epoch
from ..validation import (
    load_manifest,
    validate_jsonl_file,
    validate_relative_path,
    validate_session_id,
)

LEGACY_MACHINE_KEY = "_legacy"
LEGACY_MACHINE_LABEL = "旧布局"
LEGACY_EXPORT_GROUP = "legacy"
CUSTOM_EXPORT_GROUP = "custom"
LEGACY_ROOT_DIRS = {"bundles", "desktop_bundles"}
CANONICAL_EXPORT_GROUPS = {
    "single",
    "cli",
    "desktop",
    "active",
}
LEGACY_EXPORT_GROUP_ALIASES = {
    "single_exports": "single",
    "cli_batches": "cli",
    "desktop_all_batches": "desktop",
    "desktop_active_batches": "active",
}
KNOWN_BUNDLE_GROUPS = CANONICAL_EXPORT_GROUPS | set(LEGACY_EXPORT_GROUP_ALIASES)
EXPORT_GROUP_LABELS = {
    "single": "single",
    "cli": "cli",
    "desktop": "desktop",
    "active": "active",
    LEGACY_EXPORT_GROUP: "旧布局",
    CUSTOM_EXPORT_GROUP: "自定义目录",
}
EXPORT_GROUP_ORDER = (
    "desktop",
    "active",
    "cli",
    "single",
    LEGACY_EXPORT_GROUP,
    CUSTOM_EXPORT_GROUP,
)


def bundle_export_group_label(export_group: str) -> str:
    return EXPORT_GROUP_LABELS.get(export_group, export_group or "未知导出方式")


def canonical_export_group_name(export_group: str) -> str:
    if not export_group:
        return ""
    return LEGACY_EXPORT_GROUP_ALIASES.get(export_group, export_group)


def source_group_allows_export_group(source_group: str, export_group: str) -> bool:
    canonical = canonical_export_group_name(export_group)
    if canonical in {LEGACY_EXPORT_GROUP, CUSTOM_EXPORT_GROUP}:
        return True
    if source_group in {"", "all"}:
        return True
    if source_group == "bundle":
        return canonical in {"single", "cli"}
    if source_group == "desktop":
        return canonical in {"desktop", "active"}
    return True


def infer_bundle_machine(bundle_root: Path, bundle_dir: Path, manifest: dict) -> tuple[str, str]:
    manifest_key = manifest.get("EXPORT_MACHINE_KEY", "")
    manifest_label = manifest.get("EXPORT_MACHINE", "")
    if manifest_key:
        return manifest_key, manifest_label or manifest_key

    try:
        relative_parts = bundle_dir.relative_to(bundle_root).parts
    except ValueError:
        relative_parts = ()

    if len(relative_parts) >= 4 and relative_parts[1] in KNOWN_BUNDLE_GROUPS:
        machine_key = relative_parts[0]
        return machine_key, manifest_label or machine_key

    return LEGACY_MACHINE_KEY, manifest_label or LEGACY_MACHINE_LABEL


def infer_bundle_export_group(bundle_root: Path, bundle_dir: Path) -> tuple[str, str]:
    try:
        relative_parts = bundle_dir.relative_to(bundle_root).parts
    except ValueError:
        relative_parts = ()

    export_group = ""
    if len(relative_parts) >= 4 and relative_parts[1] in KNOWN_BUNDLE_GROUPS:
        export_group = canonical_export_group_name(relative_parts[1])
    elif len(relative_parts) >= 3 and relative_parts[0] in KNOWN_BUNDLE_GROUPS:
        export_group = canonical_export_group_name(relative_parts[0])
    else:
        export_group = CUSTOM_EXPORT_GROUP

    return export_group, bundle_export_group_label(export_group)


def collect_bundle_summaries(
    bundle_root: Path,
    *,
    source_group: str = "",
    pattern: str = "",
    machine_filter: str = "",
    export_group_filter: str = "",
    limit: Optional[int] = None,
) -> List[BundleSummary]:
    bundle_root = Path(bundle_root).expanduser()
    if not bundle_root.is_dir():
        return []
    export_group_filter = canonical_export_group_name(export_group_filter)

    summaries: List[BundleSummary] = []
    for bundle_dir in iter_bundle_directories_under_root(bundle_root):
        try:
            relative_parts = bundle_dir.relative_to(bundle_root).parts
        except ValueError:
            relative_parts = ()
        if bundle_root.name == "codex_sessions" and relative_parts and relative_parts[0] in LEGACY_ROOT_DIRS:
            continue
        manifest_file = bundle_dir / "manifest.env"
        try:
            manifest = load_manifest(manifest_file)
        except ToolkitError:
            continue
        machine_key, machine_label = infer_bundle_machine(bundle_root, bundle_dir, manifest)
        if machine_filter and machine_key != machine_filter:
            continue
        export_group, export_group_label = infer_bundle_export_group(bundle_root, bundle_dir)
        if not source_group_allows_export_group(source_group, export_group):
            continue
        if export_group_filter and export_group != export_group_filter:
            continue

        summary = BundleSummary(
            source_group=source_group,
            session_id=manifest.get("SESSION_ID", ""),
            bundle_dir=bundle_dir,
            relative_path=manifest.get("RELATIVE_PATH", ""),
            updated_at=manifest.get("UPDATED_AT", ""),
            exported_at=manifest.get("EXPORTED_AT", ""),
            thread_name=manifest.get("THREAD_NAME", ""),
            session_cwd=manifest.get("SESSION_CWD", ""),
            session_kind=manifest.get("SESSION_KIND", ""),
            source_machine=machine_label,
            source_machine_key=machine_key,
            export_group=export_group,
            export_group_label=export_group_label,
        )
        if pattern:
            combined = " ".join(
                [
                    summary.session_id,
                    summary.relative_path,
                    summary.thread_name,
                    summary.session_cwd,
                    summary.session_kind,
                    summary.source_machine,
                    summary.source_machine_key,
                    summary.export_group,
                    summary.export_group_label,
                    str(summary.bundle_dir),
                ]
            )
            if pattern not in combined:
                continue

        summaries.append(summary)
        if limit is not None and len(summaries) >= max(1, limit):
            break

    return summaries


def iter_bundle_directories_under_root(bundle_root: Path) -> List[Path]:
    bundle_root = Path(bundle_root).expanduser()
    if not bundle_root.is_dir():
        return []

    bundle_dirs: List[Path] = []
    seen_dirs: set[Path] = set()
    for manifest_file in bundle_root.rglob("manifest.env"):
        bundle_dir = manifest_file.parent
        try:
            relative_parts = bundle_dir.relative_to(bundle_root).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative_parts):
            continue
        if bundle_dir not in seen_dirs:
            bundle_dirs.append(bundle_dir)
            seen_dirs.add(bundle_dir)
    bundle_dirs.sort()
    return bundle_dirs


def bundle_directory_sort_key(bundle_dir: Path, *, manifest: Optional[dict] = None) -> Tuple[int, int, str]:
    exported_epoch = 0
    if manifest is not None:
        exported_epoch = iso_to_epoch(manifest.get("EXPORTED_AT", "") or manifest.get("UPDATED_AT", ""))
    else:
        manifest_file = bundle_dir / "manifest.env"
        try:
            m = load_manifest(manifest_file)
            exported_epoch = iso_to_epoch(m.get("EXPORTED_AT", "") or m.get("UPDATED_AT", ""))
        except Exception:
            pass
    try:
        modified_ns = bundle_dir.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return (exported_epoch, modified_ns, str(bundle_dir))


def resolve_bundle_dir(bundle_root: Path, session_id: str) -> Path:
    session_id = validate_session_id(session_id)
    bundle_root = Path(bundle_root).expanduser()

    direct_candidate = bundle_root / session_id
    candidates: List[Path] = []
    manifest_cache: dict[Path, dict] = {}
    seen: set[Path] = set()
    if (direct_candidate / "manifest.env").is_file():
        candidates.append(direct_candidate)
        seen.add(direct_candidate)
        try:
            manifest_cache[direct_candidate] = load_manifest(direct_candidate / "manifest.env")
        except Exception:
            pass

    for bundle_dir in iter_bundle_directories_under_root(bundle_root):
        if bundle_dir in seen:
            continue
        if bundle_dir.name == session_id:
            candidates.append(bundle_dir)
            seen.add(bundle_dir)
            continue
        manifest_file = bundle_dir / "manifest.env"
        try:
            manifest = load_manifest(manifest_file)
        except Exception:
            continue
        if manifest.get("SESSION_ID", "") == session_id:
            candidates.append(bundle_dir)
            seen.add(bundle_dir)
            manifest_cache[bundle_dir] = manifest

    if not candidates:
        raise ToolkitError(f"Bundle not found for session id: {session_id}")

    candidates.sort(key=lambda d: bundle_directory_sort_key(d, manifest=manifest_cache.get(d)), reverse=True)
    return candidates[0]


def resolve_known_bundle_dir(
    paths: CodexPaths,
    session_id: str,
    *,
    source_group: str = "all",
    machine_filter: str = "",
    export_group_filter: str = "",
) -> Path:
    session_id = validate_session_id(session_id)
    candidates = [
        summary.bundle_dir
        for summary in collect_known_bundle_summaries(
            paths,
            source_group=source_group,
            machine_filter=machine_filter,
            export_group_filter=export_group_filter,
            limit=None,
        )
        if summary.session_id == session_id
    ]
    if not candidates:
        raise ToolkitError(f"Bundle not found for session id: {session_id}")

    candidates.sort(key=bundle_directory_sort_key, reverse=True)
    return candidates[0]


def collect_known_bundle_summaries(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: Optional[int] = None,
    source_group: str = "all",
    machine_filter: str = "",
    export_group_filter: str = "",
) -> List[BundleSummary]:
    if source_group not in {"all", "bundle", "desktop"}:
        raise ToolkitError(f"Unsupported source_group: {source_group}")
    export_group_filter = canonical_export_group_name(export_group_filter)

    summaries: List[BundleSummary] = []
    roots: List[Tuple[str, Path, str]] = []
    roots.append(("primary", paths.default_bundle_root, source_group))
    if source_group in {"all", "bundle"}:
        roots.append(("legacy-bundle", paths.legacy_bundle_root, "bundle"))
    if source_group in {"all", "desktop"}:
        roots.append(("legacy-desktop", paths.legacy_desktop_bundle_root, "desktop"))

    seen_roots: set[Path] = set()
    for root_name, root_path, root_filter in roots:
        resolved_root = Path(root_path).expanduser()
        if resolved_root in seen_roots:
            continue
        seen_roots.add(resolved_root)
        summaries.extend(
            collect_bundle_summaries(
                resolved_root,
                source_group=root_filter,
                pattern=pattern,
                machine_filter=machine_filter,
                export_group_filter=export_group_filter,
            )
        )

    summaries.sort(
        key=lambda item: (iso_to_epoch(item.updated_at or item.exported_at), item.session_id),
        reverse=True,
    )
    if limit is not None:
        return summaries[: max(1, limit)]
    return summaries


def latest_distinct_bundle_summaries(summaries: List[BundleSummary]) -> List[BundleSummary]:
    latest: List[BundleSummary] = []
    seen_keys: set[tuple[str, str]] = set()

    for bundle in sorted(
        summaries,
        key=lambda item: (iso_to_epoch(item.updated_at or item.exported_at), str(item.bundle_dir)),
        reverse=True,
    ):
        dedupe_key = (
            bundle.source_machine_key or LEGACY_MACHINE_KEY,
            bundle.session_id,
        )
        if dedupe_key in seen_keys:
            continue
        latest.append(bundle)
        seen_keys.add(dedupe_key)

    return latest


def iter_known_bundle_directories(
    paths: CodexPaths,
    *,
    source_group: str = "all",
) -> List[Tuple[str, Path]]:
    if source_group not in {"all", "bundle", "desktop"}:
        raise ToolkitError(f"Unsupported source_group: {source_group}")

    roots: List[Tuple[str, Path, str]] = [("primary", paths.default_bundle_root, source_group)]
    if source_group in {"all", "bundle"}:
        roots.append(("legacy-bundle", paths.legacy_bundle_root, "bundle"))
    if source_group in {"all", "desktop"}:
        roots.append(("legacy-desktop", paths.legacy_desktop_bundle_root, "desktop"))

    bundle_dirs: List[Tuple[str, Path]] = []
    seen_roots: set[Path] = set()
    for group_name, root, root_filter in roots:
        root = Path(root).expanduser()
        if root in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root)
        for path in iter_bundle_directories_under_root(root):
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                relative_parts = ()
            if root.name == "codex_sessions" and relative_parts and relative_parts[0] in LEGACY_ROOT_DIRS:
                continue
            export_group, _ = infer_bundle_export_group(root, path)
            if not source_group_allows_export_group(root_filter, export_group):
                continue
            bundle_dirs.append((group_name, path))
    return bundle_dirs


def validate_bundle_directory(
    bundle_dir: Path,
    *,
    source_group: str = "",
) -> BundleValidationResult:
    bundle_dir = Path(bundle_dir).expanduser()
    manifest_file = bundle_dir / "manifest.env"
    bundle_history = bundle_dir / "history.jsonl"

    try:
        if not manifest_file.is_file():
            raise ToolkitError(f"Missing manifest: {manifest_file}")

        manifest = load_manifest(manifest_file)
        session_id = validate_session_id(manifest.get("SESSION_ID", ""))
        relative_path = validate_relative_path(manifest.get("RELATIVE_PATH", ""), session_id)

        source_session = bundle_dir / "codex" / relative_path
        ensure_path_within_dir(source_session, bundle_dir / "codex", "Bundled session file")
        validate_jsonl_file(source_session, "Bundled session file", "session", session_id)
        if bundle_history.exists():
            validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

        return BundleValidationResult(
            source_group=source_group,
            bundle_dir=bundle_dir,
            session_id=session_id,
            is_valid=True,
            message="OK",
        )
    except Exception as exc:
        fallback_session_id = bundle_dir.name
        try:
            if manifest_file.is_file():
                fallback_session_id = load_manifest(manifest_file).get("SESSION_ID", bundle_dir.name) or bundle_dir.name
        except Exception:
            pass
        return BundleValidationResult(
            source_group=source_group,
            bundle_dir=bundle_dir,
            session_id=fallback_session_id,
            is_valid=False,
            message=str(exc),
        )
