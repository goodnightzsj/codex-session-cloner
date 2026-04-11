"""Session rollout file helpers."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
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
    """Yield rollout files sorted by path within each segment (active, then archived).

    Active and archived sessions are sorted independently; the two segments are
    not merge-sorted across boundaries.  Callers that need a globally-sorted
    stream should apply their own sort on the returned iterable.
    """
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


def session_timestamp_from_filename(path: Path) -> str:
    match = re.match(r"^rollout-(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(.+)\.jsonl$", path.name)
    if not match:
        return ""
    return f"{match.group(1)} {match.group(2)}:{match.group(3)}"


def normalize_session_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def looks_like_session_meta_text(text: str) -> bool:
    normalized = normalize_session_text(text)
    if not normalized:
        return True

    return normalized.lower().startswith(
        (
            "<environment_context>",
            "<permissions instructions>",
            "<app-context>",
            "<collaboration_mode>",
            "<skills_instructions>",
            "<turn_aborted>",
            "<image",
        )
    )


def first_text_fragment(value: object) -> str:
    if isinstance(value, str):
        return normalize_session_text(value)
    if isinstance(value, list):
        for item in value:
            text = first_text_fragment(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("text", "message", "content"):
            text = first_text_fragment(value.get(key))
            if text:
                return text
    return ""


def first_user_prompt_from_session(session_file: Path) -> str:
    try:
        with session_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                payload = obj.get("payload")
                candidate = ""
                if obj.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
                    candidate = first_text_fragment(payload.get("content"))
                elif obj.get("type") == "message" and isinstance(payload, dict) and payload.get("role") == "user":
                    candidate = first_text_fragment(payload.get("text"))
                elif obj.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
                    candidate = first_text_fragment(payload.get("message") or payload.get("text"))

                if candidate and not looks_like_session_meta_text(candidate):
                    return candidate
    except FileNotFoundError:
        pass
    return ""


def workspace_name_from_cwd(cwd: str) -> str:
    normalized = (cwd or "").strip()
    if not normalized:
        return ""

    stripped = normalized.rstrip("\\/")
    if not stripped:
        return normalized

    if "\\" in stripped:
        return PureWindowsPath(stripped).name or stripped
    return Path(stripped).name or PureWindowsPath(stripped).name or stripped


def build_session_preview(history_preview: str, session_file: Path, cwd: str) -> str:
    for candidate in (history_preview, first_user_prompt_from_session(session_file)):
        normalized = normalize_session_text(candidate)
        if normalized and not looks_like_session_meta_text(normalized):
            return normalized

    workspace_name = workspace_name_from_cwd(cwd)
    timestamp_label = session_timestamp_from_filename(session_file)
    if workspace_name and timestamp_label:
        return f"{workspace_name} · {timestamp_label}"
    if workspace_name:
        return f"工作区：{workspace_name}"
    if timestamp_label:
        return f"会话开始于 {timestamp_label}"
    return session_file.name


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
                except json.JSONDecodeError as exc:
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
                except json.JSONDecodeError as exc:
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
    fields = extract_session_meta_fields(session_file, field_name)
    return fields.get(field_name, "")


def extract_session_meta_fields(session_file: Path, *field_names: str) -> dict:
    result = {name: "" for name in field_names}
    try:
        with session_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "session_meta":
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    break
                for name in field_names:
                    value = payload.get(name)
                    result[name] = value if isinstance(value, str) else ""
                break
    except FileNotFoundError:
        print(f"Warning: session file not found: {session_file}", file=sys.stderr)
    return result


def extract_last_timestamp(session_file: Path) -> str:
    last_timestamp = ""
    try:
        with session_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                timestamp = obj.get("timestamp")
                if isinstance(timestamp, str) and timestamp:
                    last_timestamp = timestamp
    except FileNotFoundError:
        pass
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
        session_scope = "archived" if "archived_sessions" in session_file.parts else "active"
        fields = extract_session_meta_fields(session_file, "source", "originator", "cwd", "model_provider")
        source_name = fields["source"]
        originator_name = fields["originator"]
        session_kind = classify_session_kind(source_name, originator_name)
        if desktop_only and session_kind != "desktop":
            continue

        cwd = fields["cwd"]
        model_provider = fields["model_provider"]
        preview = build_session_preview(history_preview.get(session_id, ""), session_file, cwd)
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
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Warning: failed to read session file {path}: {exc}", file=sys.stderr)
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
