"""CLI and TUI dispatch for cc-clean."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import APP_COMMAND, APP_DISPLAY_NAME, __version__
from .history_remap import remap_history_identifiers
from .models import PlanItem, RunOptions
from .paths import default_paths
from .services import build_plan, execute_plan, format_bytes, resolve_selection, target_keys
from .tui import run_tui
from .tui.terminal import configure_text_streams, is_interactive_terminal


def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description="%s：用于清理 Claude 本地数据的备份安全工具。" % APP_DISPLAY_NAME,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_home_arg(parser)

    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list-targets", help="显示支持的清理目标键名。")
    _add_home_arg(list_parser)
    list_parser.set_defaults(command="list-targets")

    plan_parser = subparsers.add_parser("plan", help="预览清理计划。")
    _add_home_arg(plan_parser)
    _add_selection_args(plan_parser)

    clean_parser = subparsers.add_parser("clean", help="执行清理操作。")
    _add_home_arg(clean_parser)
    _add_selection_args(clean_parser)
    clean_parser.add_argument("--yes", action="store_true", help="跳过确认提示。")

    remap_parser = subparsers.add_parser("remap-history", help="用当前新标识回写旧的结构化本地记录。")
    _add_home_arg(remap_parser)
    remap_parser.add_argument("--yes", action="store_true", help="跳过确认提示。")
    remap_parser.add_argument("--dry-run", action="store_true", help="仅预览变更，不写入磁盘。")
    remap_parser.add_argument("--no-backup", action="store_true", help="直接覆盖，不创建备份。")
    remap_parser.add_argument(
        "--run-claude",
        action="store_true",
        help="执行前先运行一次 Claude，以便生成新的活跃 userID / stableID。",
    )
    remap_parser.add_argument(
        "--claude-timeout",
        type=int,
        default=45,
        help="运行 Claude 预热时的超时秒数。",
    )
    remap_parser.add_argument(
        "--backup-root",
        default="",
        help="手动指定旧标识来源备份目录；默认自动选择最新的 cc-clean 备份。",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_text_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv and is_interactive_terminal():
        return run_tui(default_paths())

    parser = create_arg_parser()
    args = parser.parse_args(argv)
    paths = default_paths(Path(args.home))

    if args.command == "list-targets":
        for key in target_keys():
            print(key)
        return 0

    if args.command == "remap-history":
        backup_root = Path(args.backup_root).expanduser() if args.backup_root else None
        if not args.yes and not _confirm_cli("确认继续执行历史标识回写？[y/N] "):
            print("已取消。")
            return 1
        summary = remap_history_identifiers(
            paths,
            options=RunOptions(
                backup_enabled=(not args.no_backup),
                dry_run=bool(args.dry_run),
            ),
            run_claude=bool(args.run_claude),
            claude_timeout_seconds=int(args.claude_timeout),
            backup_root_hint=backup_root,
        )
        _print_execution_summary(summary)
        return 0

    if args.command in {"plan", "clean"}:
        selected = resolve_selection(
            preset=args.preset,
            include_keys=args.select,
            exclude_keys=args.exclude,
        )
        plan = build_plan(paths, selected)
        _print_plan(plan, backup_enabled=(not args.no_backup), dry_run=bool(args.dry_run))

        if args.command == "plan":
            return 0

        if not args.yes and not _confirm_cli():
            print("已取消。")
            return 1

        summary = execute_plan(
            paths,
            plan,
            RunOptions(
                backup_enabled=(not args.no_backup),
                dry_run=bool(args.dry_run),
            ),
        )
        _print_execution_summary(summary)
        return 0

    parser.print_help()
    return 0


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=("safe", "full", "none"),
        default="safe",
        help="初始目标预设。",
    )
    parser.add_argument(
        "--select",
        action="append",
        default=[],
        help="向选择集追加目标键名，可重复传入。",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="从选择集中移除目标键名，可重复传入。",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览变更，不写入磁盘。")
    parser.add_argument("--no-backup", action="store_true", help="直接删除，不创建备份。")


def _add_home_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--home",
        default=str(Path.home()),
        help="覆盖要检查的主目录路径，默认使用当前用户主目录。",
    )


def _print_plan(plan: Iterable[PlanItem], backup_enabled: bool, dry_run: bool) -> None:
    print("%s 计划" % APP_DISPLAY_NAME)
    print("=" * 72)
    print("备份：%s" % ("开启" if backup_enabled else "关闭"))
    print("演练模式：%s" % ("开启" if dry_run else "关闭"))
    print("")
    for item in plan:
        marker = "[x]" if item.selected else "[ ]"
        status = "可执行" if item.applicable else ("存在" if item.exists else "缺失")
        risk = "危险" if item.target.danger else "安全"
        print(
            "%s %-24s %-7s %-7s %8s"
            % (marker, item.target.key, status, risk, format_bytes(item.size_bytes))
        )
        print("    %s" % item.target.label)
        print("    %s" % item.details)
        for warning in item.warnings:
            print("    警告：%s" % warning)
    print("")


def _print_execution_summary(summary) -> None:
    print("")
    if summary.backup_root:
        print("备份目录：%s" % summary.backup_root)
    for record in summary.records:
        print("[%s] %s - %s" % (record.status, record.key, record.message))
        if record.backup_path:
            print("  备份：%s" % record.backup_path)


def _confirm_cli(prompt: str = "确认继续执行清理？[y/N] ") -> bool:
    try:
        response = input(prompt)
    except EOFError:
        return False
    return response.strip().lower() in {"y", "yes"}
