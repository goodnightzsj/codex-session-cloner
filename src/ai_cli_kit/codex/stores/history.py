"""History JSONL helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence


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
            except json.JSONDecodeError:
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
            except json.JSONDecodeError:
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
