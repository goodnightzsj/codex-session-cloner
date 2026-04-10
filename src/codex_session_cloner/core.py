#!/usr/bin/env python3
"""
Codex session export/import/repair toolkit.

This module ports the core behavior from the user's shell-based
`codex_sessions` scripts into Python so it can be integrated with the
interactive TUI/CLI experience in this repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import sys
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import APP_COMMAND

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None


class ToolkitError(RuntimeError):
    """Raised for expected user-facing errors."""


@dataclass(frozen=True)
class CodexPaths:
    home: Path = Path.home()

    @property
    def code_dir(self) -> Path:
        return self.home / ".codex"

    @property
    def sessions_dir(self) -> Path:
        return self.code_dir / "sessions"

    @property
    def archived_sessions_dir(self) -> Path:
        return self.code_dir / "archived_sessions"

    @property
    def history_file(self) -> Path:
        return self.code_dir / "history.jsonl"

    @property
    def index_file(self) -> Path:
        return self.code_dir / "session_index.jsonl"

    @property
    def state_file(self) -> Path:
        return self.code_dir / ".codex-global-state.json"

    @property
    def config_file(self) -> Path:
        return self.code_dir / "config.toml"

    @property
    def local_bundle_workspace(self) -> Path:
        return Path.cwd() / "codex_sessions"

    @property
    def default_bundle_root(self) -> Path:
        return self.local_bundle_workspace / "bundles"

    @property
    def default_desktop_bundle_root(self) -> Path:
        return self.local_bundle_workspace / "desktop_bundles"

    def latest_state_db(self) -> Optional[Path]:
        matches = sorted(self.code_dir.glob("state_*.sqlite"))
        return matches[-1] if matches else None


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


@dataclass(frozen=True)
class BundleValidationResult:
    source_group: str
    bundle_dir: Path
    session_id: str
    is_valid: bool
    message: str


def validate_session_id(session_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]+", session_id or ""):
        raise ToolkitError(f"Invalid session id: {session_id}")
    return session_id


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


def build_single_export_root(bundle_root: Path) -> Path:
    return Path(bundle_root).expanduser() / "single_exports" / export_batch_slug()


def build_batch_export_root(bundle_root: Path, archive_group: str) -> Path:
    return Path(bundle_root).expanduser() / archive_group / export_batch_slug()


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


def iter_session_files(paths: CodexPaths, *, active_only: bool = False) -> Iterable[Path]:
    if paths.sessions_dir.exists():
        yield from sorted(paths.sessions_dir.rglob("rollout-*.jsonl"))
    if not active_only and paths.archived_sessions_dir.exists():
        yield from sorted(paths.archived_sessions_dir.rglob("rollout-*.jsonl"))


def session_id_from_filename(path: Path) -> Optional[str]:
    name = path.name
    match = re.match(r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl$", name)
    return match.group(1) if match else None


def parse_jsonl_records(path: Path) -> List[Tuple[str, Optional[dict]]]:
    records: List[Tuple[str, Optional[dict]]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_number, raw in enumerate(fh, 1):
                stripped = raw.strip()
                if not stripped:
                    records.append((raw, None))
                    continue
                try:
                    obj = json.loads(stripped)
                except Exception as exc:
                    raise ToolkitError(f"{path} line {line_number}: {exc}") from exc
                if not isinstance(obj, dict):
                    raise ToolkitError(f"{path} line {line_number}: JSON value is not an object")
                records.append((raw, obj))
    except FileNotFoundError as exc:
        raise ToolkitError(f"Missing file: {path}") from exc
    return records


def read_session_payload(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_number, raw in enumerate(fh, 1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except Exception as exc:
                    raise ToolkitError(f"{path} line {line_number}: {exc}") from exc
                if obj.get("type") != "session_meta":
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    raise ToolkitError(f"{path} line {line_number}: session_meta payload is not an object")
                return dict(payload)
    except FileNotFoundError as exc:
        raise ToolkitError(f"Missing file: {path}") from exc

    raise ToolkitError(f"{path}: session_meta not found")


def validate_jsonl_file(
    file_path: Path,
    file_label: str,
    file_kind: str,
    expected_session_id: str = "",
) -> None:
    if not file_path.is_file():
        raise ToolkitError(f"Missing {file_label}: {file_path}")

    found_session_meta = False
    with file_path.open("r", encoding="utf-8") as fh:
        for line_number, raw in enumerate(fh, 1):
            stripped = raw.strip()
            if not stripped:
                continue

            try:
                obj = json.loads(stripped)
            except Exception as exc:
                raise ToolkitError(f"{file_label} has invalid JSON at line {line_number}: {exc}") from exc

            if not isinstance(obj, dict):
                raise ToolkitError(f"{file_label} line {line_number} is not a JSON object.")

            if file_kind == "session":
                if obj.get("type") == "session_meta":
                    found_session_meta = True
                    payload = obj.get("payload")
                    payload_session_id = payload.get("id") if isinstance(payload, dict) else None
                    if expected_session_id and payload_session_id and payload_session_id != expected_session_id:
                        raise ToolkitError(
                            f"{file_label} session_meta id does not match expected session id: {payload_session_id}"
                        )
            elif file_kind == "history":
                session_id = obj.get("session_id")
                if expected_session_id and session_id != expected_session_id:
                    raise ToolkitError(
                        f"{file_label} line {line_number} has unexpected session_id: {session_id}"
                    )

    if file_kind == "session" and not found_session_meta:
        raise ToolkitError(f"{file_label} does not contain a session_meta record.")


def extract_session_field_from_file(field_name: str, session_file: Path) -> str:
    with session_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                continue
            if obj.get("type") != "session_meta":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                break
            value = payload.get(field_name)
            return value if isinstance(value, str) else ""
    return ""


def extract_last_timestamp(session_file: Path) -> str:
    last_timestamp = ""
    with session_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                continue
            timestamp = obj.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                last_timestamp = timestamp
    return last_timestamp


def first_history_messages(history_file: Path) -> Dict[str, str]:
    first_messages: Dict[str, str] = {}
    if not history_file.exists():
        return first_messages

    with history_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            session_id = obj.get("session_id")
            text = obj.get("text")
            if isinstance(session_id, str) and session_id and session_id not in first_messages:
                if isinstance(text, str) and text:
                    first_messages[session_id] = text.replace("\n", " ")
    return first_messages


def collect_history_lines_for_session(history_file: Path, session_id: str) -> List[str]:
    lines: List[str] = []
    if not history_file.exists():
        return lines

    with history_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                continue
            if obj.get("session_id") == session_id:
                lines.append(raw if raw.endswith("\n") else raw + "\n")
    return lines


def first_history_text(history_lines: Sequence[str]) -> str:
    for raw in history_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except Exception:
            continue
        text = obj.get("text")
        if isinstance(text, str):
            return text.replace("\n", " ")
    return ""


def find_session_file(paths: CodexPaths, session_id: str) -> Optional[Path]:
    validate_session_id(session_id)
    for session_file in iter_session_files(paths):
        if session_id_from_filename(session_file) == session_id:
            return session_file
    return None


def detect_provider(paths: CodexPaths, explicit: str = "") -> str:
    if explicit:
        return explicit

    config_file = paths.config_file
    if not config_file.exists():
        raise ToolkitError(f"Missing config file: {config_file}")

    if tomllib is not None:
        try:
            with config_file.open("rb") as fh:
                data = tomllib.load(fh)
            provider = data.get("model_provider")
            if isinstance(provider, str) and provider:
                return provider
        except Exception:
            pass

    text = config_file.read_text(encoding="utf-8")
    match = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match:
        return match.group(1)

    raise ToolkitError("Could not detect model_provider from ~/.codex/config.toml")


def load_manifest(manifest_file: Path) -> Dict[str, str]:
    allowed = {
        "SESSION_ID",
        "RELATIVE_PATH",
        "EXPORTED_AT",
        "UPDATED_AT",
        "THREAD_NAME",
        "SESSION_CWD",
        "SESSION_SOURCE",
        "SESSION_ORIGINATOR",
        "SESSION_KIND",
    }
    values: Dict[str, str] = {}

    with manifest_file.open("r", encoding="utf-8") as fh:
        for line_number, raw in enumerate(fh, 1):
            raw = raw.rstrip("\n")
            if not raw or raw.startswith("#"):
                continue
            if "=" not in raw:
                raise ToolkitError(f"Invalid manifest line {line_number}: {raw}")

            key, value = raw.split("=", 1)
            if key not in allowed:
                raise ToolkitError(f"Unexpected manifest key: {key}")

            try:
                parts = shlex.split(value, posix=True)
            except ValueError as exc:
                raise ToolkitError(f"Invalid manifest value for {key}") from exc

            if len(parts) != 1:
                raise ToolkitError(f"Invalid manifest value for {key}")

            values[key] = parts[0]

    if not values.get("SESSION_ID") or not values.get("RELATIVE_PATH"):
        raise ToolkitError("Manifest is missing required fields.")
    return values


def collect_bundle_summaries(
    bundle_root: Path,
    *,
    source_group: str = "",
    pattern: str = "",
    limit: Optional[int] = None,
) -> List[BundleSummary]:
    bundle_root = Path(bundle_root).expanduser()
    if not bundle_root.is_dir():
        return []

    summaries: List[BundleSummary] = []
    for bundle_dir in iter_bundle_directories_under_root(bundle_root):
        manifest_file = bundle_dir / "manifest.env"
        try:
            manifest = load_manifest(manifest_file)
        except ToolkitError:
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
        )
        if pattern:
            combined = " ".join(
                [
                    summary.session_id,
                    summary.relative_path,
                    summary.thread_name,
                    summary.session_cwd,
                    summary.session_kind,
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


def bundle_directory_sort_key(bundle_dir: Path) -> Tuple[int, int, str]:
    manifest_file = bundle_dir / "manifest.env"
    exported_epoch = 0
    try:
        manifest = load_manifest(manifest_file)
        exported_epoch = iso_to_epoch(manifest.get("EXPORTED_AT", "") or manifest.get("UPDATED_AT", ""))
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
    if (direct_candidate / "manifest.env").is_file():
        candidates.append(direct_candidate)

    for bundle_dir in iter_bundle_directories_under_root(bundle_root):
        if bundle_dir in candidates:
            continue
        manifest_file = bundle_dir / "manifest.env"
        candidate_session_id = ""
        try:
            candidate_session_id = load_manifest(manifest_file).get("SESSION_ID", "")
        except Exception:
            pass
        if bundle_dir.name == session_id or candidate_session_id == session_id:
            candidates.append(bundle_dir)

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
) -> List[BundleSummary]:
    if source_group not in {"all", "bundle", "desktop"}:
        raise ToolkitError(f"Unsupported source_group: {source_group}")

    summaries: List[BundleSummary] = []
    if source_group in {"all", "bundle"}:
        summaries.extend(
            collect_bundle_summaries(
                paths.default_bundle_root,
                source_group="bundle",
                pattern=pattern,
            )
        )
    if source_group in {"all", "desktop"}:
        summaries.extend(
            collect_bundle_summaries(
                paths.default_desktop_bundle_root,
                source_group="desktop",
                pattern=pattern,
            )
        )

    summaries.sort(
        key=lambda item: (iso_to_epoch(item.updated_at or item.exported_at), item.session_id),
        reverse=True,
    )
    if limit is not None:
        return summaries[: max(1, limit)]
    return summaries


def iter_known_bundle_directories(
    paths: CodexPaths,
    *,
    source_group: str = "all",
) -> List[Tuple[str, Path]]:
    if source_group not in {"all", "bundle", "desktop"}:
        raise ToolkitError(f"Unsupported source_group: {source_group}")

    roots: List[Tuple[str, Path]] = []
    if source_group in {"all", "bundle"}:
        roots.append(("bundle", paths.default_bundle_root))
    if source_group in {"all", "desktop"}:
        roots.append(("desktop", paths.default_desktop_bundle_root))

    bundle_dirs: List[Tuple[str, Path]] = []
    for group_name, root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for path in iter_bundle_directories_under_root(root):
            bundle_dirs.append((group_name, path))
    return bundle_dirs


def validate_bundle_directory(
    paths: CodexPaths,
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
        relative_path = manifest.get("RELATIVE_PATH", "")
        validate_relative_path(relative_path, session_id)

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


def validate_bundles(
    paths: CodexPaths,
    *,
    pattern: str = "",
    source_group: str = "all",
    limit: Optional[int] = None,
    verbose: bool = False,
) -> int:
    bundle_entries = iter_known_bundle_directories(paths, source_group=source_group)
    results: List[BundleValidationResult] = []

    for entry_source_group, bundle_dir in bundle_entries:
        result = validate_bundle_directory(paths, bundle_dir, source_group=entry_source_group)
        if pattern:
            haystack = " ".join(
                [
                    result.source_group,
                    result.session_id,
                    str(result.bundle_dir),
                    result.message,
                ]
            )
            if pattern not in haystack:
                continue
        results.append(result)
        if limit is not None and len(results) >= max(1, limit):
            break

    valid_results = [result for result in results if result.is_valid]
    invalid_results = [result for result in results if not result.is_valid]

    print(f"Bundle source filter: {source_group}")
    print(f"Bundle directories scanned: {len(results)}")
    print(f"Valid bundles: {len(valid_results)}")
    print(f"Invalid bundles: {len(invalid_results)}")
    sys.stdout.flush()

    if verbose:
        for result in valid_results:
            print(f"[OK] [{result.source_group}] {result.session_id} | {result.bundle_dir}")

    if invalid_results:
        print("Bundle validation completed with failures.", file=sys.stderr)
        print("Invalid bundle directories:", file=sys.stderr)
        for result in invalid_results:
            print(f"[{result.source_group}] {result.bundle_dir}", file=sys.stderr)
            print(f"  session_id: {result.session_id}", file=sys.stderr)
            print(f"  reason: {result.message}", file=sys.stderr)
        return 1

    return 0


def list_bundles(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: int = 30,
    source_group: str = "all",
) -> int:
    rows: List[str] = []
    for bundle in collect_known_bundle_summaries(
        paths,
        pattern=pattern,
        limit=max(1, limit),
        source_group=source_group,
    ):
        updated = bundle.updated_at or bundle.exported_at or "-"
        title = bundle.thread_name or bundle.relative_path or bundle.session_id
        rows.append(
            f"{bundle.session_id} | {bundle.source_group} | {bundle.session_kind or '-'} | "
            f"{updated} | {bundle.bundle_dir} | {title[:80]}"
        )

    if not rows:
        print("No matching bundles found.")
        return 0

    for row in rows:
        print(row)
    return 0


def validate_relative_path(relative_path: str, session_id: str) -> None:
    if not relative_path or relative_path.startswith("/") or "\n" in relative_path:
        raise ToolkitError(f"Unsafe relative path in manifest: {relative_path}")

    if not (relative_path.startswith("sessions/") or relative_path.startswith("archived_sessions/")):
        raise ToolkitError(f"Unexpected relative path in manifest: {relative_path}")

    path = Path(relative_path)
    if any(part in {"..", "."} for part in path.parts):
        raise ToolkitError(f"Path traversal detected in manifest: {relative_path}")

    if not path.name.endswith(f"-{session_id}.jsonl"):
        raise ToolkitError(f"Manifest path does not match session id: {relative_path}")


def ensure_path_within_dir(target_path: Path, base_dir: Path, label: str) -> None:
    try:
        target_real = os.path.realpath(target_path)
        base_real = os.path.realpath(base_dir)
        common = os.path.commonpath([target_real, base_real])
    except ValueError:
        common = ""

    if common == base_real:
        return

    raise ToolkitError(f"{label} escapes base directory: {target_path}")


def normalize_updated_at(raw_value: str, session_file: Path) -> str:
    normalized = extract_iso_timestamp(raw_value)
    if not normalized and session_file.is_file():
        normalized = extract_last_timestamp(session_file)
    if not normalized:
        normalized = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return normalized


def salvage_index_line(raw: str) -> Optional[dict]:
    session_match = re.search(r'"id"\s*:\s*"([^"]+)"', raw)
    if not session_match:
        return None

    thread_match = re.search(r'"thread_name"\s*:\s*"((?:\\.|[^"])*)"', raw)
    raw_thread_name = thread_match.group(1) if thread_match else session_match.group(1)
    try:
        thread_name = json.loads(f'"{raw_thread_name}"')
    except Exception:
        thread_name = raw_thread_name.replace('\\"', '"')

    updated_match = re.search(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        raw,
    )
    return {
        "id": session_match.group(1),
        "thread_name": thread_name,
        "updated_at": updated_match.group(0) if updated_match else "",
    }


def load_existing_index(index_file: Path) -> Dict[str, dict]:
    entries: Dict[str, dict] = {}
    if not index_file.exists():
        return entries

    with index_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                obj = salvage_index_line(raw)
            if not isinstance(obj, dict):
                continue
            session_id = obj.get("id")
            if isinstance(session_id, str) and session_id:
                entries[session_id] = {
                    "thread_name": obj.get("thread_name") or session_id,
                    "updated_at": normalize_iso(str(obj.get("updated_at", ""))),
                }
    return entries


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


def backup_file(code_dir: Path, backup_root: Path, backed_up: set[str], path: Path, *, enabled: bool) -> None:
    if not enabled or not path.exists():
        return
    resolved = str(path.resolve())
    if resolved in backed_up:
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / path.relative_to(code_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    backed_up.add(resolved)


def upsert_session_index(index_file: Path, session_id: str, thread_name: str, updated_at: str) -> None:
    entries = OrderedDict()
    discarded_invalid_lines = 0

    if index_file.exists():
        with index_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    obj = salvage_index_line(raw)
                    if obj is None:
                        discarded_invalid_lines += 1
                        continue

                if not isinstance(obj, dict):
                    continue

                existing_id = obj.get("id")
                if not existing_id or existing_id == session_id:
                    continue

                normalized = {
                    "id": existing_id,
                    "thread_name": obj.get("thread_name") or existing_id,
                    "updated_at": normalize_iso(str(obj.get("updated_at", ""))) or updated_at,
                }

                if existing_id in entries:
                    del entries[existing_id]
                entries[existing_id] = normalized

    entries[session_id] = {
        "id": session_id,
        "thread_name": thread_name or session_id,
        "updated_at": updated_at,
    }

    index_file.parent.mkdir(parents=True, exist_ok=True)
    with index_file.open("w", encoding="utf-8") as fh:
        for obj in entries.values():
            fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    if discarded_invalid_lines:
        print(
            f"Warning: discarded {discarded_invalid_lines} unrecoverable malformed session_index.jsonl line(s).",
            file=sys.stderr,
        )


def ensure_desktop_workspace_root(workspace_dir: str, state_file: Path) -> bool:
    if not state_file.exists():
        print(f"Warning: Codex Desktop state file not found: {state_file}", file=sys.stderr)
        return False

    with state_file.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    saved = list(data.setdefault("electron-saved-workspace-roots", []))
    project_order = list(data.setdefault("project-order", []))

    covered = False
    for root in saved:
        if workspace_dir == root or workspace_dir.startswith(root.rstrip("/") + "/"):
            covered = True
            break

    if not covered:
        saved.append(workspace_dir)
        project_order.append(workspace_dir)

    data["electron-saved-workspace-roots"] = saved
    data["project-order"] = project_order

    with state_file.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return True


def prepare_session_for_import(
    source_session: Path,
    prepared_session: Path,
    *,
    auto_desktop_compat: bool,
    session_kind: str,
    target_desktop_model_provider: str,
) -> None:
    with source_session.open("r", encoding="utf-8") as in_fh, prepared_session.open("w", encoding="utf-8") as out_fh:
        for raw in in_fh:
            line = raw.rstrip("\n")
            if not line:
                out_fh.write(raw)
                continue

            try:
                obj = json.loads(line)
            except Exception:
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

    first_user_message = thread_name
    if history_file.exists():
        with history_file.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
            if first_line:
                try:
                    first_user_message = json.loads(first_line).get("text") or first_user_message
                except Exception:
                    pass

    source_name = session_source or meta.get("source", "")
    originator_name = session_originator or meta.get("originator", "")
    effective_kind = session_kind or classify_session_kind(source_name, originator_name)
    cwd = session_cwd or meta.get("cwd", "")
    created_iso = meta.get("timestamp") or last_timestamp or updated_at
    updated_iso = updated_at or last_timestamp or created_iso
    title = thread_name or first_user_message or session_id
    sandbox_policy = json.dumps(turn_context.get("sandbox_policy", {}), ensure_ascii=False, separators=(",", ":"))
    approval_mode = turn_context.get("approval_policy", "on-request")
    model_provider = meta.get("model_provider", "")
    cli_version = meta.get("cli_version", "")
    model = turn_context.get("model")
    reasoning_effort = turn_context.get("effort")
    archived = 1 if "/archived_sessions/" in str(target_rollout) else 0
    archived_at = iso_to_epoch(updated_iso) if archived else None

    conn = sqlite3.connect(state_db)
    cur = conn.cursor()
    row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
    if not row:
        conn.close()
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
    conn.close()
    return True


def list_sessions(paths: CodexPaths, *, pattern: str = "", limit: int = 30) -> int:
    rows: List[str] = []
    for summary in collect_session_summaries(paths, pattern=pattern, limit=max(1, limit)):
        rows.append(
            f"{summary.session_id} | {summary.kind} | {summary.scope} | "
            f"{summary.model_provider or '-'} | {summary.path} | {summary.preview[:80]}"
        )

    if not rows:
        print("No matching sessions found.")
        return 0

    for row in rows:
        print(row)
    return 0


def collect_session_summaries(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: Optional[int] = None,
    active_only: bool = False,
    desktop_only: bool = False,
) -> List[SessionSummary]:
    history_preview = first_history_messages(paths.history_file)
    summaries: List[SessionSummary] = []

    for session_file in sorted(iter_session_files(paths, active_only=active_only), reverse=True):
        session_id = session_id_from_filename(session_file) or session_file.stem
        session_scope = "archived" if str(session_file).startswith(str(paths.archived_sessions_dir)) else "active"
        preview = history_preview.get(session_id, "")
        source_name = extract_session_field_from_file("source", session_file)
        originator_name = extract_session_field_from_file("originator", session_file)
        session_kind = classify_session_kind(source_name, originator_name)
        if desktop_only and session_kind != "desktop":
            continue

        cwd = extract_session_field_from_file("cwd", session_file)
        model_provider = extract_session_field_from_file("model_provider", session_file)
        summary = SessionSummary(
            session_id=session_id,
            scope=session_scope,
            path=session_file,
            preview=preview,
            kind=session_kind,
            cwd=cwd,
            model_provider=model_provider,
        )

        if pattern:
            combined = " ".join(
                [
                    summary.session_id,
                    summary.scope,
                    summary.kind,
                    summary.model_provider,
                    summary.cwd,
                    summary.preview,
                    str(summary.path),
                ]
            )
            if pattern not in combined:
                continue

        summaries.append(summary)
        if limit is not None and len(summaries) >= max(1, limit):
            break

    return summaries


def extract_timestamp_from_rollout_name(filename: str) -> str:
    match = re.match(r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-", filename)
    return match.group(1) if match else ""


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
        except ToolkitError:
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
) -> Tuple[str, str, Optional[Path]]:
    session_file = Path(session_file).expanduser()
    if not session_file.is_file():
        raise ToolkitError(f"Missing session file: {session_file}")

    provider = detect_provider(paths, explicit=target_provider)
    if already_cloned_ids is None:
        already_cloned_ids = build_clone_index(paths, target_provider=provider, quiet=True)

    try:
        records = parse_jsonl_records(session_file)
    except ToolkitError as exc:
        return "error", str(exc), None

    if not records:
        return "error", "Empty file", None

    meta_index = -1
    session_meta: dict = {}
    for idx, (_, obj) in enumerate(records):
        if obj and obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
            meta_index = idx
            session_meta = dict(obj)
            break

    if meta_index < 0:
        return "error", "Not a session file", None

    payload = dict(session_meta["payload"])
    current_provider = payload.get("model_provider", "")
    current_id = payload.get("id")

    if not isinstance(current_id, str) or not current_id:
        return "error", "Session id missing from session_meta", None

    if current_provider == provider:
        return "skipped_target", "Already on target provider", None

    if current_id in already_cloned_ids:
        return "skipped_exists", f"Already cloned (ID: {current_id})", None

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
        return "skipped_exists", "Target file collision", None

    output_lines: List[str] = []
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
    return "cloned", message, new_file_path


def clone_to_provider(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = True,
) -> int:
    provider = detect_provider(paths, explicit=target_provider)
    already_cloned = build_clone_index(paths, target_provider=provider, active_only=active_only)
    stats = {
        "cloned": 0,
        "skipped_exists": 0,
        "skipped_target": 0,
        "error": 0,
    }

    print("\nScanning candidates...")
    for session_file in iter_session_files(paths, active_only=active_only):
        action, message, _ = clone_session_file(
            paths,
            session_file,
            target_provider=provider,
            already_cloned_ids=already_cloned,
            dry_run=dry_run,
        )
        stats[action] = stats.get(action, 0) + 1
        if action == "cloned":
            print(f"[+] {message}")
        elif action == "error":
            print(f"[!] {session_file.name}: {message}", file=sys.stderr)

    print("\n==============================")
    print("Summary:")
    print(f"  Target Provider: {provider}")
    print(f"  Cloned (New):    {stats['cloned']}")
    print(f"  Skipped (Target):{stats['skipped_target']} (already on target provider)")
    print(f"  Skipped (Done):  {stats['skipped_exists']} (already cloned earlier)")
    print(f"  Errors:          {stats['error']}")
    print("==============================")

    if dry_run:
        print("\nThis was a DRY RUN. No files were created.")
    return 0


def cleanup_clones(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = True,
) -> int:
    provider = detect_provider(paths, explicit=target_provider)
    print("Scanning for unmarked clones to clean up...")

    originals_by_ts: set[str] = set()
    targets_without_tag_by_ts: Dict[str, List[Path]] = {}
    files_checked = 0

    for session_file in iter_session_files(paths, active_only=active_only):
        files_checked += 1
        timestamp = extract_timestamp_from_rollout_name(session_file.name)
        if not timestamp:
            continue

        try:
            payload = read_session_payload(session_file)
        except ToolkitError:
            continue

        current_provider = payload.get("model_provider", "")
        cloned_from = payload.get("cloned_from")
        if current_provider == provider:
            if not isinstance(cloned_from, str) or not cloned_from:
                targets_without_tag_by_ts.setdefault(timestamp, []).append(session_file)
        else:
            originals_by_ts.add(timestamp)

    files_to_delete: List[Path] = []
    for timestamp, paths_for_ts in targets_without_tag_by_ts.items():
        if timestamp in originals_by_ts:
            files_to_delete.extend(paths_for_ts)

    print(f"Scanned {files_checked} files. Found {len(files_to_delete)} unmarked clones.")
    for target_path in files_to_delete:
        if dry_run:
            print(f"[DRY-RUN] Would delete: {target_path}")
            continue
        try:
            target_path.unlink()
            print(f"[Deleted] {target_path}")
        except Exception as exc:
            print(f"[Error] Deleting {target_path}: {exc}", file=sys.stderr)

    print("\nCleanup scan complete.")
    return 0


def export_session(
    paths: CodexPaths,
    session_id: str,
    *,
    bundle_root: Optional[Path] = None,
    quiet: bool = False,
) -> Path:
    session_id = validate_session_id(session_id)
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
    stage_root = Path(tempfile.mkdtemp(prefix=f".{session_id}.tmp.", dir=str(bundle_root)))
    stage_bundle_dir = stage_root / session_id
    old_bundle_backup: Optional[Path] = None

    try:
        bundle_codex_dir = stage_bundle_dir / "codex"
        bundle_history = stage_bundle_dir / "history.jsonl"
        manifest_file = stage_bundle_dir / "manifest.env"

        (bundle_codex_dir / relative_path.parent).mkdir(parents=True, exist_ok=True)

        bundled_session = bundle_codex_dir / relative_path
        shutil.copy2(session_file, bundled_session)
        validate_jsonl_file(bundled_session, "Bundled session file", "session", session_id)

        history_lines = collect_history_lines_for_session(paths.history_file, session_id)
        bundle_history.parent.mkdir(parents=True, exist_ok=True)
        with bundle_history.open("w", encoding="utf-8") as fh:
            fh.writelines(history_lines)
        validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

        first_prompt = first_history_text(history_lines)
        session_cwd = extract_session_field_from_file("cwd", session_file)
        session_source = extract_session_field_from_file("source", session_file)
        session_originator = extract_session_field_from_file("originator", session_file)
        session_kind = classify_session_kind(session_source, session_originator)
        last_updated = extract_last_timestamp(session_file) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        manifest_data = OrderedDict(
            SESSION_ID=session_id,
            RELATIVE_PATH=str(relative_path),
            EXPORTED_AT=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            UPDATED_AT=last_updated,
            THREAD_NAME=first_prompt[:80],
            SESSION_CWD=session_cwd,
            SESSION_SOURCE=session_source,
            SESSION_ORIGINATOR=session_originator,
            SESSION_KIND=session_kind,
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

        if not quiet:
            print(f"Exported {session_id}")
            print(f"Bundle: {final_bundle_dir}")
            print(f"Session file: {relative_path}")
            print(f"Session kind: {session_kind or 'unknown'}")
            print(f"Session cwd: {session_cwd or 'unknown'}")
        return final_bundle_dir
    except Exception:
        if old_bundle_backup and old_bundle_backup.exists() and not final_bundle_dir.exists():
            old_bundle_backup.rename(final_bundle_dir)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def collect_session_ids_for_kind(
    paths: CodexPaths,
    *,
    session_kind: str,
    active_only: bool = False,
) -> List[str]:
    session_ids: List[str] = []
    seen_session_ids: set[str] = set()

    for path in iter_session_files(paths, active_only=active_only):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    obj = json.loads(stripped)
                    if obj.get("type") != "session_meta":
                        continue
                    payload = obj.get("payload")
                    if not isinstance(payload, dict):
                        break
                    session_id = payload.get("id")
                    source_name = payload.get("source", "")
                    originator_name = payload.get("originator", "")
                    if (
                        isinstance(session_id, str)
                        and session_id
                        and classify_session_kind(source_name, originator_name) == session_kind
                        and session_id not in seen_session_ids
                    ):
                        session_ids.append(session_id)
                        seen_session_ids.add(session_id)
                    break
        except Exception:
            continue

    return session_ids


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
) -> int:
    session_ids = collect_session_ids_for_kind(paths, session_kind=session_kind, active_only=active_only)
    export_root = build_batch_export_root(bundle_root, archive_group) if not dry_run else build_batch_export_root(bundle_root, archive_group)

    print(f"Bundle root: {bundle_root}")
    print(f"Export batch: {export_root}")
    print(f"Dry run: {'yes' if dry_run else 'no'}")
    print(f"Active only: {'yes' if active_only else 'no'}")
    print(f"Session kind filter: {session_kind}")
    print(f"{summary_label} sessions found: {len(session_ids)}")

    if not session_ids:
        return 0

    if dry_run:
        for session_id in session_ids:
            print(session_id)
        return 0

    export_root.mkdir(parents=True, exist_ok=True)
    success_ids: List[str] = []
    failed_exports: List[Tuple[str, str]] = []

    for session_id in session_ids:
        try:
            export_session(paths, session_id, bundle_root=export_root, quiet=True)
            success_ids.append(session_id)
        except Exception as exc:
            failed_exports.append((session_id, str(exc)))

    manifest_file = export_root / f"_{manifest_stem}_export_manifest.txt"
    with manifest_file.open("w", encoding="utf-8") as fh:
        fh.write(f"# exported_at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        fh.write(f"# session_kind={session_kind}\n")
        fh.write(f"# active_only={1 if active_only else 0}\n")
        fh.write(f"# count={len(success_ids)}\n")
        for session_id in success_ids:
            fh.write(session_id + "\n")

    print(f"Exported {summary_label} sessions: {len(success_ids)}")
    print(f"Manifest: {manifest_file}")

    if failed_exports:
        print("Batch export completed with partial failures.")
        sys.stdout.flush()
        print(f"Failed exports: {len(failed_exports)}", file=sys.stderr)
        for session_id, reason in failed_exports:
            print(session_id, file=sys.stderr)
            print(f"  reason: {reason}", file=sys.stderr)
        return 1
    return 0


def export_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
    active_only: bool = False,
) -> int:
    return export_sessions_for_kind(
        paths,
        session_kind="desktop",
        bundle_root=normalize_bundle_root(paths, bundle_root, paths.default_desktop_bundle_root),
        dry_run=dry_run,
        active_only=active_only,
        manifest_stem=("active_desktop" if active_only else "desktop"),
        summary_label=("Active Desktop" if active_only else "Desktop"),
        archive_group=("desktop_active_batches" if active_only else "desktop_all_batches"),
    )


def export_active_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    return export_desktop_all(paths, bundle_root=bundle_root, dry_run=dry_run, active_only=True)


def export_cli_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    return export_sessions_for_kind(
        paths,
        session_kind="cli",
        bundle_root=normalize_bundle_root(paths, bundle_root, paths.default_bundle_root),
        dry_run=dry_run,
        active_only=False,
        manifest_stem="cli",
        summary_label="CLI",
        archive_group="cli_batches",
    )


def import_session(
    paths: CodexPaths,
    input_value: str,
    *,
    bundle_root: Optional[Path] = None,
    desktop_visible: bool = False,
    quiet: bool = False,
) -> int:
    bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
    input_path = Path(input_value).expanduser()
    resolved_from_session_id = False
    if input_path.is_dir():
        bundle_dir = restrict_to_local_bundle_workspace(paths, input_path, "Bundle directory")
    else:
        bundle_dir = resolve_bundle_dir(bundle_root, input_value)
        resolved_from_session_id = True

    manifest_file = bundle_dir / "manifest.env"
    bundle_history = bundle_dir / "history.jsonl"
    if not manifest_file.is_file():
        raise ToolkitError(f"Missing manifest: {manifest_file}")

    manifest = load_manifest(manifest_file)
    session_id = validate_session_id(manifest["SESSION_ID"])
    relative_path = manifest["RELATIVE_PATH"]
    validate_relative_path(relative_path, session_id)

    if not Path(input_value).expanduser().is_dir() and input_value != session_id:
        raise ToolkitError(f"Manifest session id does not match requested session id: {session_id}")

    source_session = bundle_dir / "codex" / relative_path
    target_session = paths.code_dir / relative_path

    ensure_path_within_dir(source_session, bundle_dir / "codex", "Bundled session file")
    validate_jsonl_file(source_session, "Bundled session file", "session", session_id)
    if bundle_history.exists():
        validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

    session_cwd = manifest.get("SESSION_CWD", "") or extract_session_field_from_file("cwd", source_session)
    session_source = manifest.get("SESSION_SOURCE", "") or extract_session_field_from_file("source", source_session)
    session_originator = manifest.get("SESSION_ORIGINATOR", "") or extract_session_field_from_file("originator", source_session)
    session_kind = manifest.get("SESSION_KIND", "") or classify_session_kind(session_source, session_originator)
    updated_at = normalize_updated_at(manifest.get("UPDATED_AT", ""), source_session)
    thread_name = manifest.get("THREAD_NAME", "")

    state_db = paths.latest_state_db()
    desktop_env = paths.state_file.exists() or state_db is not None
    target_desktop_model_provider = detect_provider(paths) if desktop_env else ""
    auto_desktop_compat = session_kind == "cli" and desktop_env

    prepared_fd, prepared_path = tempfile.mkstemp(prefix="codex-import-session.")
    os.close(prepared_fd)
    Path(prepared_path).unlink(missing_ok=True)
    prepared_source_session = Path(prepared_path)

    try:
        prepare_session_for_import(
            source_session,
            prepared_source_session,
            auto_desktop_compat=auto_desktop_compat,
            session_kind=session_kind,
            target_desktop_model_provider=target_desktop_model_provider,
        )
        validate_jsonl_file(prepared_source_session, "Prepared session file", "session", session_id)

        import_mode = "native"
        if auto_desktop_compat and session_kind == "cli":
            session_source = "vscode"
            session_originator = "Codex Desktop"
            session_kind = "desktop"
            import_mode = "desktop-compatible"

        if session_cwd and not Path(session_cwd).is_dir():
            if desktop_visible:
                Path(session_cwd).mkdir(parents=True, exist_ok=True)
                print(f"Created missing workspace directory: {session_cwd}", file=sys.stderr)
            else:
                quoted_cwd = shlex.quote(session_cwd)
                print(f"Warning: missing workspace directory: {session_cwd}", file=sys.stderr)
                print(
                    "This session may not appear in the Codex Desktop sidebar until that directory exists.",
                    file=sys.stderr,
                )
                print("Create it when convenient with:", file=sys.stderr)
                print(f"mkdir -p -- {quoted_cwd}", file=sys.stderr)
                print("Continuing with import anyway.", file=sys.stderr)

        target_session.parent.mkdir(parents=True, exist_ok=True)
        if target_session.exists() and prepared_source_session.read_bytes() != target_session.read_bytes():
            backup_path = target_session.with_name(target_session.name + f".bak.{int(datetime.now().timestamp())}")
            shutil.copy2(target_session, backup_path)
            print(f"Backed up existing session file to {backup_path}")

        shutil.copy2(prepared_source_session, target_session)

        paths.history_file.parent.mkdir(parents=True, exist_ok=True)
        paths.history_file.touch(exist_ok=True)
        existing_history_lines = set(paths.history_file.read_text(encoding="utf-8").splitlines())
        if bundle_history.exists():
            with bundle_history.open("r", encoding="utf-8") as fh_in, paths.history_file.open("a", encoding="utf-8") as fh_out:
                for raw in fh_in:
                    stripped = raw.rstrip("\n")
                    if not stripped or stripped in existing_history_lines:
                        continue
                    fh_out.write(raw if raw.endswith("\n") else raw + "\n")
                    existing_history_lines.add(stripped)

        upsert_session_index(paths.index_file, session_id, thread_name or f"Imported {session_id}", updated_at)

        desktop_registered = "no"
        desktop_registration_target = ""
        if session_cwd:
            if Path(session_cwd).is_dir():
                desktop_registration_target = session_cwd
            else:
                desktop_registration_target = nearest_existing_parent(session_cwd)
                if desktop_registration_target and desktop_registration_target != session_cwd:
                    print(
                        "Warning: exact workspace directory is missing, using existing parent for Desktop registration: "
                        f"{desktop_registration_target}",
                        file=sys.stderr,
                    )
        if desktop_registration_target:
            if ensure_desktop_workspace_root(desktop_registration_target, paths.state_file):
                desktop_registered = "yes"

        thread_row_upserted = "yes" if state_db and upsert_threads_table(
            state_db,
            prepared_source_session,
            bundle_history,
            target_session,
            session_id=session_id,
            thread_name=thread_name,
            updated_at=updated_at,
            session_cwd=session_cwd,
            session_source=session_source,
            session_originator=session_originator,
            session_kind=session_kind,
        ) else "no"

        if not quiet:
            if resolved_from_session_id:
                print(f"Resolved bundle directory: {bundle_dir}")
            print(f"Imported {session_id}")
            print(f"Session file: {relative_path}")
            print(f"Import mode: {import_mode}")
            print(f"Session kind: {session_kind or 'unknown'}")
            print(f"Workspace group: {session_cwd or 'unknown'}")
            print(f"Desktop workspace registered: {desktop_registered}")
            print(f"Desktop registration target: {desktop_registration_target or 'none'}")
            print(f"Threads table upserted: {thread_row_upserted}")
            if target_desktop_model_provider:
                print(f"Desktop model provider: {target_desktop_model_provider}")
        return 0
    finally:
        prepared_source_session.unlink(missing_ok=True)


def import_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    desktop_visible: bool = False,
) -> int:
    bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_desktop_bundle_root)
    if not bundle_root.is_dir():
        raise ToolkitError(f"Missing bundle root: {bundle_root}")

    bundle_dirs = iter_bundle_directories_under_root(bundle_root)

    print(f"Bundle root: {bundle_root}")
    print(f"Desktop visible: {'yes' if desktop_visible else 'no'}")
    print(f"Bundle directories found: {len(bundle_dirs)}")

    if not bundle_dirs:
        return 0

    success_dirs: List[Path] = []
    failed_imports: List[Tuple[Path, str]] = []
    for bundle_dir in bundle_dirs:
        try:
            import_session(paths, str(bundle_dir), bundle_root=bundle_root, desktop_visible=desktop_visible, quiet=True)
            success_dirs.append(bundle_dir)
        except Exception as exc:
            failed_imports.append((bundle_dir, str(exc)))

    print(f"Imported bundle directories: {len(success_dirs)}")
    if failed_imports:
        print("Batch import completed with partial failures.")
        sys.stdout.flush()
        print(f"Failed imports: {len(failed_imports)}", file=sys.stderr)
        for failed_dir, reason in failed_imports:
            print(str(failed_dir), file=sys.stderr)
            print(f"  reason: {reason}", file=sys.stderr)
        return 1
    return 0


def repair_desktop(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    include_cli: bool = False,
) -> int:
    if not paths.code_dir.is_dir():
        raise ToolkitError(f"Missing Codex data directory: {paths.code_dir}")

    provider = detect_provider(paths, explicit=target_provider)
    backup_root = paths.code_dir / "repair_backups" / f"visibility-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backed_up: set[str] = set()
    warnings: List[str] = []

    history_first_messages = first_history_messages(paths.history_file)
    existing_index = load_existing_index(paths.index_file)
    state_db = paths.latest_state_db()

    entries: List[dict] = []
    changed_sessions: List[str] = []
    skipped_sessions: List[str] = []
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

        source_name = session_meta.get("source", "")
        originator_name = session_meta.get("originator", "")
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
                with session_file.open("w", encoding="utf-8") as fh:
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
        thread_name = existing_index.get(session_id, {}).get("thread_name") or history_first_messages.get(session_id) or session_id
        created_iso = normalize_iso(str(session_meta.get("timestamp", ""))) or normalize_iso(last_timestamp)
        updated_iso = (
            normalize_iso(last_timestamp)
            or created_iso
            or existing_index.get(session_id, {}).get("updated_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        cwd = session_meta.get("cwd", "") if isinstance(session_meta.get("cwd", ""), str) else ""
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
                "first_user_message": history_first_messages.get(session_id, thread_name),
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
        paths.index_file.parent.mkdir(parents=True, exist_ok=True)
        with paths.index_file.open("w", encoding="utf-8") as fh:
            for entry in entries:
                obj = {
                    "id": entry["id"],
                    "thread_name": entry["thread_name"],
                    "updated_at": entry["updated_at"],
                }
                fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    if paths.state_file.exists():
        state_data = json.loads(paths.state_file.read_text(encoding="utf-8"))
    else:
        state_data = {}

    saved_roots = list(state_data.get("electron-saved-workspace-roots", []))
    project_order = list(state_data.get("project-order", []))

    for root in workspace_candidates:
        covered = False
        for existing in saved_roots:
            existing_str = str(existing)
            if root == existing_str or root.startswith(existing_str.rstrip("/") + "/"):
                covered = True
                break
        if not covered:
            saved_roots.append(root)
            if root not in project_order:
                project_order.append(root)

    state_data["electron-saved-workspace-roots"] = saved_roots
    state_data["active-workspace-roots"] = list(saved_roots)
    state_data["project-order"] = project_order

    if not dry_run:
        backup_file(paths.code_dir, backup_root, backed_up, paths.state_file, enabled=True)
        paths.state_file.parent.mkdir(parents=True, exist_ok=True)
        paths.state_file.write_text(json.dumps(state_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    threads_updated = 0
    if state_db and state_db.exists():
        if not dry_run:
            backup_file(paths.code_dir, backup_root, backed_up, state_db, enabled=True)
        conn = sqlite3.connect(state_db)
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
                    "source": entry["source"] or "vscode",
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
                values = [data[name] for name in insert_cols]
                sql = f"insert into threads ({col_list}) values ({placeholders}) on conflict(id) do update set {update_sql}"
                if not dry_run:
                    cur.execute(sql, values)
                threads_updated += 1

            if not dry_run:
                conn.commit()
        else:
            warnings.append(f"threads table not found in {state_db}")
        conn.close()

    print(f"Target model provider: {provider}")
    print(f"Dry run: {'yes' if dry_run else 'no'}")
    print(f"Include CLI: {'yes' if include_cli else 'no'}")
    print(f"Valid session files scanned: {len(entries)}")
    print(f"Desktop session files retagged: {desktop_retagged}")
    print(f"CLI session files converted: {cli_converted}")
    print(f"Skipped invalid session files: {len(skipped_sessions)}")
    print(f"Workspace roots active after repair: {len(state_data.get('active-workspace-roots', []))}")
    print(f"Desktop thread rows upserted: {threads_updated}")
    if not dry_run:
        print(f"Backup directory: {backup_root}")

    if changed_sessions:
        print("Changed session files:")
        for path_str in changed_sessions[:20]:
            print(path_str)
        if len(changed_sessions) > 20:
            print(f"... and {len(changed_sessions) - 20} more")

    if warnings:
        print("Warnings:", file=sys.stderr)
        for warning in warnings:
            print(warning, file=sys.stderr)
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description="Codex session clone/export/import/repair toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List local sessions")
    list_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")

    list_bundles_parser = subparsers.add_parser("list-bundles", help="List available bundle directories")
    list_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_bundles_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")
    list_bundles_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Bundle source group to list",
    )

    validate_bundles_parser = subparsers.add_parser("validate-bundles", help="Validate exported bundle directories")
    validate_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    validate_bundles_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Bundle source group to validate",
    )
    validate_bundles_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for validation count (0 means no limit)",
    )
    validate_bundles_parser.add_argument("--verbose", action="store_true", help="Print successful bundle validations too")

    clone_parser = subparsers.add_parser("clone-provider", help="Clone active sessions to the target provider")
    clone_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clone_parser.add_argument("--dry-run", action="store_true")

    clean_parser = subparsers.add_parser("clean-clones", help="Delete legacy unmarked clone files")
    clean_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clean_parser.add_argument("--dry-run", action="store_true")

    export_parser = subparsers.add_parser("export", help="Export one session bundle")
    export_parser.add_argument("session_id")

    export_all_parser = subparsers.add_parser("export-desktop-all", help="Export all Desktop sessions in bulk")
    export_all_parser.add_argument("--dry-run", action="store_true")
    export_all_parser.add_argument("--active-only", action="store_true", help="Legacy compatibility flag")

    export_active_desktop_parser = subparsers.add_parser(
        "export-active-desktop-all",
        help="Export all active Desktop sessions in bulk",
    )
    export_active_desktop_parser.add_argument("--dry-run", action="store_true")

    export_cli_parser = subparsers.add_parser("export-cli-all", help="Export all CLI sessions in bulk")
    export_cli_parser.add_argument("--dry-run", action="store_true")

    import_parser = subparsers.add_parser("import", help="Import one session bundle")
    import_parser.add_argument("input_value", help="Session id or bundle directory")
    import_parser.add_argument("--desktop-visible", action="store_true")

    import_all_parser = subparsers.add_parser("import-desktop-all", help="Import Desktop bundles in bulk")
    import_all_parser.add_argument("--desktop-visible", action="store_true")

    repair_parser = subparsers.add_parser("repair-desktop", help="Repair Desktop sidebar visibility")
    repair_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.add_argument("--include-cli", action="store_true")

    return parser


def run_cli(argv: Sequence[str], *, paths: Optional[CodexPaths] = None) -> int:
    paths = paths or CodexPaths()
    parser = create_parser()
    args = parser.parse_args(list(argv))

    if args.command == "list":
        return list_sessions(paths, pattern=args.pattern, limit=max(1, args.limit))
    if args.command == "list-bundles":
        return list_bundles(
            paths,
            pattern=args.pattern,
            limit=max(1, args.limit),
            source_group=args.source,
        )
    if args.command == "validate-bundles":
        return validate_bundles(
            paths,
            pattern=args.pattern,
            source_group=args.source,
            limit=(None if args.limit <= 0 else args.limit),
            verbose=args.verbose,
        )
    if args.command == "clone-provider":
        return clone_to_provider(paths, target_provider=args.target_provider, dry_run=args.dry_run)
    if args.command == "clean-clones":
        return cleanup_clones(paths, target_provider=args.target_provider, dry_run=args.dry_run)
    if args.command == "export":
        export_session(
            paths,
            args.session_id,
            bundle_root=build_single_export_root(paths.default_bundle_root),
        )
        return 0
    if args.command == "export-desktop-all":
        return export_desktop_all(paths, dry_run=args.dry_run, active_only=args.active_only)
    if args.command == "export-active-desktop-all":
        return export_active_desktop_all(paths, dry_run=args.dry_run)
    if args.command == "export-cli-all":
        return export_cli_all(paths, dry_run=args.dry_run)
    if args.command == "import":
        return import_session(paths, args.input_value, desktop_visible=args.desktop_visible)
    if args.command == "import-desktop-all":
        return import_desktop_all(paths, desktop_visible=args.desktop_visible)
    if args.command == "repair-desktop":
        return repair_desktop(
            paths,
            target_provider=args.target_provider,
            dry_run=args.dry_run,
            include_cli=args.include_cli,
        )

    raise ToolkitError(f"Unknown command: {args.command}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        return run_cli(argv)
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
