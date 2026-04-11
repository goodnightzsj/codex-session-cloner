"""Provider resolution helpers."""

from __future__ import annotations

import re

from ..errors import ToolkitError
from ..paths import CodexPaths

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None


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
