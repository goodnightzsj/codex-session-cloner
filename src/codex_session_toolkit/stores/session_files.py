"""Session rollout file helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from ..errors import ToolkitError
from ..models import SessionSummary
from ..paths import CodexPaths
from ..support import classify_session_kind
from ..validation import validate_session_id
from .history import first_history_messages

_ROLLOUT_FILENAME_RE = re.compile(
    r"^rollout-(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-"
    r"(?P<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\.jsonl$"
)


def iter_session_files(paths: CodexPaths, *, active_only: bool = False) -> Iterable[Path]:
    if paths.sessions_dir.exists():
        yield from sorted(paths.sessions_dir.rglob("rollout-*.jsonl"))
    if not active_only and paths.archived_sessions_dir.exists():
        yield from sorted(paths.archived_sessions_dir.rglob("rollout-*.jsonl"))


def session_id_from_filename(path: Path) -> Optional[str]:
    match = _ROLLOUT_FILENAME_RE.match(path.name)
    return match.group("id") if match else None


def extract_session_id_from_filename(filename: str) -> Optional[str]:
    match = _ROLLOUT_FILENAME_RE.match(filename)
    return match.group("id") if match else None


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


def find_session_file(paths: CodexPaths, session_id: str) -> Optional[Path]:
    validate_session_id(session_id)
    for session_file in iter_session_files(paths):
        if session_id_from_filename(session_file) == session_id:
            return session_file
    return None


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


def extract_timestamp_from_rollout_name(filename: str) -> str:
    match = _ROLLOUT_FILENAME_RE.match(filename)
    return match.group("ts") if match else ""


def parse_codex_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def clone_timestamp_token(session_file: Path, meta: dict, payload: dict) -> str:
    existing_token = extract_timestamp_from_rollout_name(session_file.name)
    if existing_token:
        return existing_token
    for candidate in (payload.get("timestamp"), meta.get("timestamp")):
        parsed = parse_codex_timestamp(candidate)
        if parsed:
            return parsed.strftime("%Y-%m-%dT%H-%M-%S")
    try:
        return datetime.fromtimestamp(session_file.stat().st_mtime).strftime("%Y-%m-%dT%H-%M-%S")
    except OSError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def build_canonical_clone_path(paths: CodexPaths, session_file: Path, meta: dict, payload: dict, new_id: str) -> Path:
    ts = clone_timestamp_token(session_file, meta, payload)
    date_token = ts.split("T", 1)[0]
    year, month, day = date_token.split("-")
    return paths.sessions_dir / year / month / day / f"rollout-{ts}-{new_id}.jsonl"


def is_codex_rollout_compatible(paths: CodexPaths, file_path: Path, session_id: Optional[str]) -> bool:
    ts = extract_timestamp_from_rollout_name(file_path.name)
    filename_sid = extract_session_id_from_filename(file_path.name)
    if not ts or not filename_sid:
        return False
    if session_id and filename_sid.lower() != str(session_id).lower():
        return False
    date_token = ts.split("T", 1)[0]
    year, month, day = date_token.split("-")
    expected_parent = paths.sessions_dir / year / month / day
    return file_path.parent == expected_parent
