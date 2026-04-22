"""Planning and execution helpers for local Claude cleanup."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..core.support import atomic_write, long_path, safe_copy2
from .models import CleanupTarget, ExecutionRecord, ExecutionSummary, PlanItem, RunOptions
from .paths import ClaudePaths


def _remove_with_retry(path: Path) -> None:
    """``shutil.rmtree``/``Path.unlink`` with bounded Windows retry.

    Windows AV scanners and indexers briefly hold files open after we touch
    them, surfacing as transient ``PermissionError`` on remove. POSIX has no
    such race, so we short-circuit there.
    """
    if path.is_dir():
        remover = lambda p=path: shutil.rmtree(long_path(p))
    else:
        remover = lambda p=path: os.unlink(long_path(p))

    if os.name != "nt":
        remover()
        return

    last_exc: Optional[BaseException] = None
    base_delay = 0.02
    for attempt in range(5):
        try:
            remover()
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(base_delay * (2 ** attempt))
    if last_exc is not None:
        raise last_exc


def _move_with_retry(src: Path, dst: Path) -> None:
    """``shutil.move`` honouring Windows long paths + transient lock retry."""
    src_str = long_path(src)
    dst_str = long_path(dst)
    if os.name != "nt":
        shutil.move(src_str, dst_str)
        return

    last_exc: Optional[BaseException] = None
    base_delay = 0.02
    for attempt in range(5):
        try:
            shutil.move(src_str, dst_str)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(base_delay * (2 ** attempt))
    if last_exc is not None:
        raise last_exc

AUTH_ENV_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

TARGET_ORDER = (
    "state_user_id",
    "telemetry_dir",
    "statsig_dir",
    "credentials_file",
    "settings_auth_env",
    "projects_dir",
    "history_file",
    "sessions_dir",
)

SAFE_TARGET_KEYS = (
    "state_user_id",
    "telemetry_dir",
    "statsig_dir",
    "credentials_file",
)

FULL_TARGET_KEYS = TARGET_ORDER


def build_targets(paths: ClaudePaths) -> Tuple[CleanupTarget, ...]:
    return (
        CleanupTarget(
            key="state_user_id",
            label="清理 ~/.claude.json 中的 userID",
            description="仅移除顶层 userID 字段，不删除整个文件。",
            action="scrub_json_fields",
            target_path=str(paths.state_file),
            json_fields=("userID",),
            default_selected=True,
        ),
        CleanupTarget(
            key="telemetry_dir",
            label="删除 ~/.claude/telemetry",
            description="清理失败遥测缓存。",
            action="remove_path",
            target_path=str(paths.telemetry_dir),
            default_selected=True,
        ),
        CleanupTarget(
            key="statsig_dir",
            label="删除 ~/.claude/statsig",
            description="清理 Statsig 稳定 ID、会话 ID 和缓存评估结果。",
            action="remove_path",
            target_path=str(paths.statsig_dir),
            default_selected=True,
        ),
        CleanupTarget(
            key="credentials_file",
            label="删除 ~/.claude/.credentials.json",
            description="如果存在这个回退凭据文件，就删除其中的明文本地凭据。",
            action="remove_path",
            target_path=str(paths.credentials_file),
            default_selected=True,
        ),
        CleanupTarget(
            key="settings_auth_env",
            label="清理 settings.json 中的自定义鉴权环境变量",
            description="从 env 中移除 ANTHROPIC_AUTH_TOKEN 和 ANTHROPIC_BASE_URL。",
            action="scrub_settings_env",
            target_path=str(paths.settings_file),
            env_keys=AUTH_ENV_KEYS,
            default_selected=False,
        ),
        CleanupTarget(
            key="projects_dir",
            label="删除 ~/.claude/projects",
            description="删除项目对话历史，可能会丢失旧的项目会话。",
            action="remove_path",
            target_path=str(paths.projects_dir),
            default_selected=False,
            danger=True,
            may_remove_sessions=True,
        ),
        CleanupTarget(
            key="history_file",
            label="删除 ~/.claude/history.jsonl",
            description="删除全局命令/历史日志。",
            action="remove_path",
            target_path=str(paths.history_file),
            default_selected=False,
            danger=True,
            may_remove_sessions=True,
        ),
        CleanupTarget(
            key="sessions_dir",
            label="删除 ~/.claude/sessions",
            description="删除本地保存的 session 文件。",
            action="remove_path",
            target_path=str(paths.sessions_dir),
            default_selected=False,
            danger=True,
            may_remove_sessions=True,
        ),
    )


def resolve_selection(
    preset: str = "safe",
    include_keys: Optional[Sequence[str]] = None,
    exclude_keys: Optional[Sequence[str]] = None,
) -> Set[str]:
    if preset not in {"safe", "full", "none"}:
        raise ValueError("preset must be one of: safe, full, none")

    selected: Set[str]
    if preset == "safe":
        selected = set(SAFE_TARGET_KEYS)
    elif preset == "full":
        selected = set(FULL_TARGET_KEYS)
    else:
        selected = set()

    include_keys = tuple(include_keys or ())
    exclude_keys = tuple(exclude_keys or ())
    unknown = (set(include_keys) | set(exclude_keys)) - set(TARGET_ORDER)
    if unknown:
        unknown_text = ", ".join(sorted(unknown))
        raise ValueError("unknown cleanup target(s): %s" % unknown_text)

    selected.update(include_keys)
    selected.difference_update(exclude_keys)
    return selected


def build_plan(paths: ClaudePaths, selected_keys: Optional[Iterable[str]] = None) -> Tuple[PlanItem, ...]:
    selected = set(selected_keys if selected_keys is not None else SAFE_TARGET_KEYS)
    plan: List[PlanItem] = []
    for target in build_targets(paths):
        if target.action == "remove_path":
            item = _inspect_remove_path(target, selected)
        elif target.action == "scrub_json_fields":
            item = _inspect_json_fields(target, selected)
        elif target.action == "scrub_settings_env":
            item = _inspect_settings_env(target, selected)
        else:
            raise ValueError("unsupported cleanup action: %s" % target.action)
        plan.append(item)
    return tuple(plan)


def execute_plan(paths: ClaudePaths, plan: Sequence[PlanItem], options: RunOptions) -> ExecutionSummary:
    backup_root: Optional[Path] = None
    records: List[ExecutionRecord] = []

    for item in plan:
        if not item.selected:
            continue

        if not item.applicable:
            records.append(
                ExecutionRecord(
                    key=item.target.key,
                    status="skipped",
                    message=item.details,
                )
            )
            continue

        target_path = Path(item.target.target_path)
        if options.dry_run:
            records.append(
                ExecutionRecord(
                    key=item.target.key,
                    status="dry-run",
                    message="Would apply %s to %s." % (item.target.action, item.target.target_path),
                )
            )
            continue

        try:
            if item.target.action == "remove_path":
                backup_root = _ensure_backup_root(paths, backup_root, options)
                record = _execute_remove_path(paths, target_path, item, backup_root, options)
            elif item.target.action == "scrub_json_fields":
                backup_root = _ensure_backup_root(paths, backup_root, options)
                record = _execute_scrub_json_fields(paths, target_path, item, backup_root, options)
            elif item.target.action == "scrub_settings_env":
                backup_root = _ensure_backup_root(paths, backup_root, options)
                record = _execute_scrub_settings_env(paths, target_path, item, backup_root, options)
            else:
                raise ValueError("unsupported cleanup action: %s" % item.target.action)
        except Exception as exc:
            record = ExecutionRecord(
                key=item.target.key,
                status="error",
                message=str(exc),
            )

        records.append(record)

    return ExecutionSummary(
        records=tuple(records),
        backup_root=str(backup_root) if backup_root is not None else None,
    )


def format_bytes(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(max(0, size_bytes))
    unit = units[0]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            break
        size /= 1024.0
    if unit == "B":
        return "%d %s" % (int(size), unit)
    return "%.1f %s" % (size, unit)


def target_keys() -> Tuple[str, ...]:
    return TARGET_ORDER


def _inspect_remove_path(target: CleanupTarget, selected: Set[str]) -> PlanItem:
    path = Path(target.target_path)
    exists = path.exists()
    size_bytes = _path_size(path) if exists else 0
    details = "路径存在，可以执行清理。" if exists else "路径已不存在。"
    warnings = ("该目标可能删除旧会话。",) if target.may_remove_sessions else ()
    return PlanItem(
        target=target,
        selected=target.key in selected,
        exists=exists,
        applicable=exists,
        size_bytes=size_bytes,
        details=details,
        warnings=warnings,
    )


def _inspect_json_fields(target: CleanupTarget, selected: Set[str]) -> PlanItem:
    path = Path(target.target_path)
    if not path.exists():
        return PlanItem(
            target=target,
            selected=target.key in selected,
            exists=False,
            applicable=False,
            size_bytes=0,
            details="文件不存在。",
        )

    payload, warnings = _load_json_dict(path)
    matches = []
    if payload is not None:
        matches = [field for field in target.json_fields if field in payload]
    details = "命中字段：%s。" % ", ".join(matches) if matches else "字段已不存在。"
    return PlanItem(
        target=target,
        selected=target.key in selected,
        exists=True,
        applicable=bool(matches),
        size_bytes=_path_size(path),
        details=details,
        warnings=tuple(warnings),
    )


def _inspect_settings_env(target: CleanupTarget, selected: Set[str]) -> PlanItem:
    path = Path(target.target_path)
    if not path.exists():
        return PlanItem(
            target=target,
            selected=target.key in selected,
            exists=False,
            applicable=False,
            size_bytes=0,
            details="settings.json 不存在。",
        )

    payload, warnings = _load_json_dict(path)
    env = payload.get("env") if isinstance(payload, dict) else None
    matches = [key for key in target.env_keys if isinstance(env, dict) and key in env]
    details = "命中环境变量：%s。" % ", ".join(matches) if matches else "没有匹配到鉴权环境变量。"
    return PlanItem(
        target=target,
        selected=target.key in selected,
        exists=True,
        applicable=bool(matches),
        size_bytes=_path_size(path),
        details=details,
        warnings=tuple(warnings),
    )


def _ensure_backup_root(paths: ClaudePaths, current: Optional[Path], options: RunOptions) -> Optional[Path]:
    if not options.backup_enabled:
        return current
    if current is not None:
        return current
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = paths.backup_root_base / stamp
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root


def _execute_remove_path(
    paths: ClaudePaths,
    path: Path,
    item: PlanItem,
    backup_root: Optional[Path],
    options: RunOptions,
) -> ExecutionRecord:
    if options.backup_enabled:
        if backup_root is None:
            raise RuntimeError("backup root was not created")
        destination = _backup_destination(paths.home, backup_root, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _move_with_retry(path, destination)
        return ExecutionRecord(
            key=item.target.key,
            status="moved",
            message="已移入备份，并从原位置移除。",
            backup_path=str(destination),
        )

    _remove_with_retry(path)
    return ExecutionRecord(
        key=item.target.key,
        status="deleted",
        message="未备份，已直接删除。",
    )


def _execute_scrub_json_fields(
    paths: ClaudePaths,
    path: Path,
    item: PlanItem,
    backup_root: Optional[Path],
    options: RunOptions,
) -> ExecutionRecord:
    payload, _ = _load_json_dict(path)
    if payload is None:
        raise RuntimeError("无法解析 JSON：%s" % path)
    backup_path = None
    if options.backup_enabled:
        if backup_root is None:
            raise RuntimeError("backup root was not created")
        backup_path = _backup_file_copy(paths.home, backup_root, path)
    for field in item.target.json_fields:
        payload.pop(field, None)
    _write_json(path, payload)
    return ExecutionRecord(
        key=item.target.key,
        status="updated",
        message="已移除字段：%s。" % ", ".join(item.target.json_fields),
        backup_path=str(backup_path) if backup_path is not None else None,
    )


def _execute_scrub_settings_env(
    paths: ClaudePaths,
    path: Path,
    item: PlanItem,
    backup_root: Optional[Path],
    options: RunOptions,
) -> ExecutionRecord:
    payload, _ = _load_json_dict(path)
    if payload is None:
        raise RuntimeError("无法解析 settings JSON：%s" % path)
    env = payload.get("env")
    if not isinstance(env, dict):
        raise RuntimeError("settings.json 中不包含 env 对象")

    backup_path = None
    if options.backup_enabled:
        if backup_root is None:
            raise RuntimeError("backup root was not created")
        backup_path = _backup_file_copy(paths.home, backup_root, path)

    for key in item.target.env_keys:
        env.pop(key, None)
    if not env:
        payload.pop("env", None)
    _write_json(path, payload)
    return ExecutionRecord(
        key=item.target.key,
        status="updated",
        message="已移除 settings 环境变量：%s。" % ", ".join(item.target.env_keys),
        backup_path=str(backup_path) if backup_path is not None else None,
    )


def _load_json_dict(path: Path) -> Tuple[Optional[Dict[str, object]], List[str]]:
    warnings: List[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append("JSON 解析失败：%s" % exc)
        return None, warnings
    if not isinstance(payload, dict):
        warnings.append("JSON 根节点不是对象。")
        return None, warnings
    return payload, warnings


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    """Atomic JSON write — survives crash/SIGKILL/AC pull mid-write.

    The previous direct ``path.write_text`` could leave ``~/.claude.json`` or
    ``settings.json`` in a half-written state if the process was interrupted
    after truncate but before the full payload landed. ``atomic_write``
    funnels the payload through a tempfile + ``os.replace`` so observers
    only ever see the old content or the new content, never a torn write.

    ``newline=""`` keeps Python from translating ``\\n`` → ``\\r\\n`` on
    Windows, matching how Claude Code's own writers emit these files.
    """
    serialized = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    with atomic_write(path) as fh:
        fh.write(serialized)


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _backup_destination(home: Path, backup_root: Path, source: Path) -> Path:
    relative = _relative_under_home(home, source)
    candidate = backup_root / relative
    if not candidate.exists():
        return candidate
    suffix = 1
    while True:
        replacement = candidate.parent / ("%s.%d" % (candidate.name, suffix))
        if not replacement.exists():
            return replacement
        suffix += 1


def _backup_file_copy(home: Path, backup_root: Path, source: Path) -> Path:
    destination = _backup_destination(home, backup_root, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # safe_copy2 applies the \\?\ long-path prefix on Windows so backups of
    # deeply nested project files (>260 chars) don't fail with ENAMETOOLONG.
    safe_copy2(source, destination)
    return destination


def _relative_under_home(home: Path, source: Path) -> Path:
    """Strip ``home`` from ``source`` for the backup mirror tree.

    On Windows NTFS / macOS APFS-default the filesystem is case-insensitive
    but ``Path.relative_to`` does a literal compare — ``C:\\Users\\Foo`` and
    ``c:\\users\\foo`` would be treated as distinct, sending genuinely-local
    files into the ``external/`` escape branch. ``os.path.normcase`` collapses
    that on case-insensitive platforms; on POSIX it's the identity, so the
    behaviour is unchanged there.
    """
    try:
        return source.relative_to(home)
    except ValueError:
        pass

    home_norm = os.path.normcase(str(home))
    source_norm = os.path.normcase(str(source))
    if source_norm.startswith(home_norm + os.sep):
        # Use the original (cased) tail so the backup mirror keeps the
        # filesystem's preferred capitalisation visible to the user.
        relative_str = str(source)[len(str(home)) + 1:] if str(source).lower().startswith(str(home).lower()) else source_norm[len(home_norm) + 1:]
        return Path(relative_str)

    cleaned = str(source).replace(":", "").lstrip("/").replace("\\", "/")
    return Path("external") / cleaned
