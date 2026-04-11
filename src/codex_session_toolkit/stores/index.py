"""Session index JSONL helpers."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Optional

from ..support import normalize_iso


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
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(index_file.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            for obj in entries.values():
                fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp_path, str(index_file))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    if discarded_invalid_lines:
        print(
            f"Warning: discarded {discarded_invalid_lines} unrecoverable malformed session_index.jsonl line(s).",
            file=sys.stderr,
        )
