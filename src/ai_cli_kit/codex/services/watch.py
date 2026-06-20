"""Provider watch helpers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Optional

from ..errors import ToolkitError
from ..models import ProviderWatchEvent
from ..paths import CodexPaths
from .clone import clone_to_provider
from .provider import detect_provider

DEFAULT_WATCH_INTERVAL_SECONDS = 2.0


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_watch_interval(interval_seconds: float) -> float:
    try:
        normalized = float(interval_seconds)
    except (TypeError, ValueError) as exc:
        raise ToolkitError("watch interval must be a number of seconds") from exc
    if normalized <= 0:
        raise ToolkitError("watch interval must be greater than 0 seconds")
    return normalized


def check_provider_watch(
    paths: CodexPaths,
    *,
    previous_provider: str = "",
    dry_run: bool = False,
    sync_on_start: bool = True,
    active_only: bool = True,
) -> ProviderWatchEvent:
    provider = detect_provider(paths)
    is_first_check = not previous_provider
    changed = bool(previous_provider) and provider != previous_provider
    should_sync = changed or (is_first_check and sync_on_start)
    clone_result = None

    if should_sync:
        clone_result = clone_to_provider(
            paths,
            target_provider=provider,
            dry_run=dry_run,
            active_only=active_only,
        )

    return ProviderWatchEvent(
        provider=provider,
        previous_provider=previous_provider,
        checked_at=_utc_timestamp(),
        changed=changed,
        clone_result=clone_result,
    )


def iter_provider_watch_events(
    paths: CodexPaths,
    *,
    interval_seconds: float = DEFAULT_WATCH_INTERVAL_SECONDS,
    dry_run: bool = False,
    sync_on_start: bool = True,
    active_only: bool = True,
    max_checks: Optional[int] = None,
    sleep_func: Callable[[float], None] = time.sleep,
) -> Iterator[ProviderWatchEvent]:
    interval_seconds = normalize_watch_interval(interval_seconds)
    if max_checks is not None and max_checks < 1:
        raise ToolkitError("max watch checks must be at least 1")

    previous_provider = ""
    checks = 0
    while max_checks is None or checks < max_checks:
        try:
            event = check_provider_watch(
                paths,
                previous_provider=previous_provider,
                dry_run=dry_run,
                sync_on_start=sync_on_start if not previous_provider else False,
                active_only=active_only,
            )
        except ToolkitError as exc:
            event = ProviderWatchEvent(
                provider=previous_provider,
                previous_provider=previous_provider,
                checked_at=_utc_timestamp(),
                changed=False,
                error=str(exc),
            )
        else:
            previous_provider = event.provider

        yield event
        checks += 1
        if max_checks is None or checks < max_checks:
            sleep_func(interval_seconds)
