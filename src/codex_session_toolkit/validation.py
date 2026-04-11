"""Validation helpers for sessions, bundles, and manifests."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from .errors import ToolkitError
from .support import ensure_path_within_dir, extract_iso_timestamp


def validate_session_id(session_id: str) -> str:
    if not session_id:
        raise ToolkitError("Session id must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9-]+", session_id):
        raise ToolkitError(f"Invalid session id: {session_id}")
    return session_id


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
        "EXPORT_MACHINE",
        "EXPORT_MACHINE_KEY",
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
            except json.JSONDecodeError as exc:
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


def normalize_relative_path(relative_path: str) -> str:
    """Normalize Windows backslashes to forward slashes and collapse duplicates."""
    normalized = (relative_path or "").replace("\\", "/").strip()
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized


def validate_relative_path(relative_path: str, session_id: str) -> str:
    """Validate and return the normalized relative path."""
    normalized = normalize_relative_path(relative_path)

    if not normalized or normalized.startswith("/") or "\n" in normalized or re.match(r"^[A-Za-z]:", normalized):
        raise ToolkitError(f"Unsafe relative path in manifest: {relative_path}")

    if not (normalized.startswith("sessions/") or normalized.startswith("archived_sessions/")):
        raise ToolkitError(f"Unexpected relative path in manifest: {relative_path}")

    path = Path(normalized)
    if any(part in {"..", "."} for part in path.parts):
        raise ToolkitError(f"Path traversal detected in manifest: {relative_path}")

    if not path.name.endswith(f"-{session_id}.jsonl"):
        raise ToolkitError(f"Manifest path does not match session id: {relative_path}")

    return normalized


def normalize_updated_at(raw_value: str, session_file: Path, fallback_timestamp: str = "") -> str:
    normalized = extract_iso_timestamp(raw_value)
    if not normalized:
        normalized = fallback_timestamp
    if not normalized:
        normalized = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return normalized


__all__ = [
    "ensure_path_within_dir",
    "load_manifest",
    "normalize_relative_path",
    "normalize_updated_at",
    "validate_jsonl_file",
    "validate_relative_path",
    "validate_session_id",
]
