"""
TUI application layer for the Codex Session Toolkit.

This module owns interactive menu composition, browser flows, and
action orchestration so the legacy entrypoint can stay focused on
argument compatibility and command dispatch.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from . import APP_COMMAND
from .core import (
    BundleSummary,
    CodexPaths,
    SessionSummary,
    ToolkitError,
    build_clone_index,
    cleanup_clones,
    clone_session_file,
    clone_to_provider,
    collect_known_bundle_summaries,
    collect_session_summaries,
    run_cli as run_toolkit_cli,
)
from .terminal_ui import (
    Ansi,
    align_line,
    app_logo_lines,
    clear_screen,
    ellipsize_middle,
    glyphs,
    read_key,
    render_box,
    style_text,
    term_height,
    term_width,
    tui_width,
)


@dataclass(frozen=True)
class ToolkitAppContext:
    target_provider: str
    active_sessions_dir: str
    config_path: str
    bundle_root_label: str = "./codex_sessions/bundles"
    desktop_bundle_root_label: str = "./codex_sessions/desktop_bundles"
    entry_command: str = APP_COMMAND


@dataclass(frozen=True)
class TuiMenuAction:
    action_id: str
    hotkey: str
    label: str
    section_id: str
    cli_args: Tuple[str, ...]
    is_dangerous: bool = False
    is_dry_run: bool = False


@dataclass(frozen=True)
class TuiMenuSection:
    title: str
    section_id: str
    border_codes: Tuple[str, ...]


TUI_ACTION_NOTES = {
    "clone": ["会为非当前 provider 的会话生成带血缘信息的新副本。"],
    "clone_dry": ["只预览将创建哪些 clone，不写入任何文件。"],
    "clean": ["删除早期版本生成、但没有 cloned_from 标记的旧副本。", "执行前需要输入 DELETE 二次确认。"],
    "clean_dry": ["只预览哪些旧副本会被删除。"],
    "list_sessions": ["内置会话浏览器，支持过滤、预览和详情查看。"],
    "browse_bundles": ["独立浏览 Bundle 仓库，而不是只在导入时顺手选择。", "支持来源过滤、详情查看和直接导入。"],
    "validate_bundles": ["扫描 Bundle 仓库里的 manifest、session JSONL 和 history JSONL。", "适合在批量导入前先找出坏包。"],
    "export_one": ["从会话列表中选择要导出的 session。", "默认归档到 ./codex_sessions/bundles/single_exports/<timestamp>/。"],
    "export_desktop_all": ["默认归档到 ./codex_sessions/desktop_bundles/desktop_all_batches/<timestamp>/。", "范围包含 active + archived 的 Desktop 会话，并分别生成 Bundle。"],
    "export_desktop_active": ["默认归档到 ./codex_sessions/desktop_bundles/desktop_active_batches/<timestamp>/。", "仅导出 ~/.codex/sessions/ 下的 Desktop 会话，不会扫描 ~/.codex/archived_sessions/。"],
    "export_cli_all": ["默认归档到 ./codex_sessions/bundles/cli_batches/<timestamp>/。", "范围包含 active + archived 的 CLI 会话，并分别生成 Bundle。"],
    "import_one": ["从 Bundle 列表中选择要导入为会话的条目。", "可选：自动创建缺失工作目录。", "导入时会顺手修复 history / index / Desktop 元数据。"],
    "import_desktop_all": ["默认从 ./codex_sessions/desktop_bundles/ 递归导入全部 Desktop Bundle 为会话。", "可选：自动创建缺失工作目录。"],
    "repair_desktop": ["对齐 provider、重建 session_index、补 threads 表与工作区根目录。"],
    "repair_desktop_dry": ["只预览将修改哪些会话和索引，不真正写入。"],
    "repair_desktop_cli": ["会把旧 CLI 线程改写成 Desktop 兼容元数据。"],
    "repair_desktop_cli_dry": ["预览哪些 CLI 线程会被纳入 Desktop 视图。"],
    "exit": ["退出工具箱。"],
}

SECTION_NOTES = {
    "provider": [
        "切换 provider 后继续复用会话。",
        "包含 clone 与旧版副本清理两类动作。",
    ],
    "browse": [
        "浏览本机会话与 Bundle 仓库。",
        "包含导出、导入与跨设备迁移入口。",
    ],
    "repair": [
        "修复 Desktop 可见性与索引一致性。",
        "适合处理 threads / provider / workspace roots 问题。",
    ],
}

FIXED_THEME_LOGO_WIDTH = 100


def build_tui_menu_actions() -> List[TuiMenuAction]:
    return [
        TuiMenuAction("clone", "1", "克隆到当前 provider", "provider", ("clone-provider",)),
        TuiMenuAction("clone_dry", "2", "模拟克隆（Dry-run）", "provider", ("clone-provider", "--dry-run"), is_dry_run=True),
        TuiMenuAction("clean", "3", "清理旧版无标记副本", "provider", ("clean-clones",), is_dangerous=True),
        TuiMenuAction("clean_dry", "4", "模拟清理旧版副本", "provider", ("clean-clones", "--dry-run"), is_dangerous=True, is_dry_run=True),
        TuiMenuAction("list_sessions", "l", "浏览最近会话", "browse", ("list", "--limit", "20")),
        TuiMenuAction("browse_bundles", "o", "浏览 Bundle 仓库", "browse", ("list-bundles", "--limit", "20")),
        TuiMenuAction("validate_bundles", "y", "校验 Bundle 仓库", "browse", ("validate-bundles", "--source", "all")),
        TuiMenuAction("export_one", "e", "导出单个会话为 Bundle", "browse", ("export", "<session_id>")),
        TuiMenuAction("export_desktop_all", "b", "批量导出全部 Desktop 会话为 Bundle", "browse", ("export-desktop-all",)),
        TuiMenuAction("export_desktop_active", "a", "批量导出全部 Active Desktop 会话为 Bundle", "browse", ("export-active-desktop-all",)),
        TuiMenuAction("export_cli_all", "c", "批量导出全部 CLI 会话为 Bundle", "browse", ("export-cli-all",)),
        TuiMenuAction("import_one", "i", "导入单个 Bundle 为会话", "browse", ("import", "<session_id|bundle_dir>")),
        TuiMenuAction("import_desktop_all", "m", "批量导入全部 Desktop Bundle 为会话", "browse", ("import-desktop-all",)),
        TuiMenuAction("repair_desktop", "r", "修复 Desktop 可见性", "repair", ("repair-desktop",)),
        TuiMenuAction("repair_desktop_dry", "v", "模拟修复 Desktop", "repair", ("repair-desktop", "--dry-run"), is_dry_run=True),
        TuiMenuAction("repair_desktop_cli", "x", "修复并纳入 CLI 线程", "repair", ("repair-desktop", "--include-cli")),
        TuiMenuAction("repair_desktop_cli_dry", "g", "模拟修复并纳入 CLI", "repair", ("repair-desktop", "--include-cli", "--dry-run"), is_dry_run=True),
        TuiMenuAction("exit", "0", "退出", "system", tuple()),
    ]


def build_tui_menu_sections() -> List[TuiMenuSection]:
    return [
        TuiMenuSection("Provider / Clone", "provider", (Ansi.DIM, Ansi.CYAN)),
        TuiMenuSection("Browse / Bundle", "browse", (Ansi.DIM, Ansi.MAGENTA)),
        TuiMenuSection("Desktop Repair", "repair", (Ansi.DIM, Ansi.GREEN)),
    ]


def format_bundle_source_label(source_group: str) -> str:
    return {
        "all": "全部来源",
        "bundle": "Bundle",
        "desktop": "Desktop Bundle",
    }.get(source_group, source_group)


def run_clone_mode(*, target_provider: str, dry_run: bool) -> int:
    try:
        return int(clone_to_provider(CodexPaths(), target_provider=target_provider, dry_run=dry_run))
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def run_cleanup_mode(
    *,
    target_provider: str,
    dry_run: bool,
    delete_warning: Optional[str] = None,
) -> int:
    if delete_warning and not dry_run:
        print(style_text(delete_warning, Ansi.BOLD, Ansi.YELLOW))
    try:
        return int(cleanup_clones(CodexPaths(), target_provider=target_provider, dry_run=dry_run))
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


class ToolkitTuiApp:
    def __init__(self, context: ToolkitAppContext) -> None:
        self.context = context
        self.paths = CodexPaths()
        self.menu_actions = build_tui_menu_actions()
        self.menu_sections = build_tui_menu_sections()
        self.hotkey_to_index = {menu_action.hotkey: idx for idx, menu_action in enumerate(self.menu_actions)}

    def _cli_preview(self, args: Sequence[str]) -> str:
        cmd = self.context.entry_command
        if args:
            cmd += " " + " ".join(args)
        return cmd

    def _screen_layout(self) -> Tuple[int, bool]:
        box_width = tui_width(term_width())
        return box_width, box_width >= 70

    def _screen_height(self) -> int:
        return max(12, term_height())

    def _fit_lines_to_screen(self, lines: List[str]) -> List[str]:
        max_rows = self._screen_height()
        if len(lines) <= max_rows:
            return lines

        visible_rows = max(6, max_rows - 1)
        trimmed = lines[:visible_rows]
        trimmed[-1] = style_text("... 窗口高度不足，内容已折叠；可放大终端窗口继续查看 ...", Ansi.DIM, Ansi.YELLOW)
        return trimmed

    def _section_tabs_line(self, selected_section_index: int, width: int) -> str:
        tabs: List[str] = []
        for pos, menu_section in enumerate(self.menu_sections):
            label = f"[{pos + 1}] {menu_section.title}"
            if pos == selected_section_index:
                tabs.append(style_text(label, Ansi.BOLD, self._section_color(menu_section)))
            else:
                tabs.append(style_text(label, Ansi.DIM))
        return ellipsize_middle("  ".join(tabs), width)

    def _action_window(self, total_count: int, selected_offset: int, max_visible: int) -> Tuple[int, int]:
        if total_count <= 0:
            return 0, 0
        max_visible = max(1, min(max_visible, total_count))
        start = max(0, selected_offset - max_visible // 2)
        start = min(start, max(0, total_count - max_visible))
        return start, min(total_count, start + max_visible)

    def _actions_for_section(self, section_id: str) -> List[Tuple[int, TuiMenuAction]]:
        return [
            (idx, menu_action)
            for idx, menu_action in enumerate(self.menu_actions)
            if menu_action.section_id == section_id
        ]

    def _print_branded_header(self, title: str, subtitle: str = "") -> int:
        clear_screen()
        box_width, center = self._screen_layout()
        for line in app_logo_lines(max_width=FIXED_THEME_LOGO_WIDTH):
            print(align_line(line, box_width, center=center))
        print(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
        print(align_line(style_text(title, Ansi.DIM), box_width, center=center))
        if subtitle:
            print(align_line(style_text(subtitle, Ansi.DIM), box_width, center=center))
        print("")
        return box_width

    def _run_toolkit(self, cli_args: List[str]) -> int:
        try:
            return int(run_toolkit_cli(cli_args))
        except ToolkitError as exc:
            print(style_text(str(exc), Ansi.RED))
            return 1

    def _action_color(self, menu_action: TuiMenuAction) -> str:
        if menu_action.is_dangerous and not menu_action.is_dry_run:
            return Ansi.RED
        if menu_action.is_dry_run:
            return Ansi.YELLOW
        if menu_action.section_id == "browse":
            return Ansi.MAGENTA
        if menu_action.section_id == "repair":
            return Ansi.GREEN
        if menu_action.action_id == "exit":
            return Ansi.DIM
        return Ansi.CYAN

    def _action_notes(self, menu_action: TuiMenuAction) -> List[str]:
        return TUI_ACTION_NOTES.get(menu_action.action_id, [])

    def _section_color(self, menu_section: TuiMenuSection) -> str:
        if menu_section.section_id == "browse":
            return Ansi.MAGENTA
        if menu_section.section_id == "repair":
            return Ansi.GREEN
        return Ansi.CYAN

    def _section_notes(self, menu_section: TuiMenuSection) -> List[str]:
        return SECTION_NOTES.get(menu_section.section_id, [])

    def _session_detail_lines(self, summary: SessionSummary) -> List[str]:
        return [
            f"{style_text('Session ID', Ansi.DIM)} : {summary.session_id}",
            f"{style_text('类型', Ansi.DIM)}      : {summary.kind}",
            f"{style_text('范围', Ansi.DIM)}      : {summary.scope}",
            f"{style_text('Provider', Ansi.DIM)}  : {summary.model_provider or '-'}",
            f"{style_text('路径', Ansi.DIM)}      : {summary.path}",
            f"{style_text('工作目录', Ansi.DIM)}  : {summary.cwd or '（空）'}",
            f"{style_text('预览', Ansi.DIM)}      : {summary.preview or '（无）'}",
        ]

    def _bundle_detail_lines(self, bundle: BundleSummary) -> List[str]:
        return [
            f"{style_text('Session ID', Ansi.DIM)} : {bundle.session_id}",
            f"{style_text('来源分组', Ansi.DIM)}  : {format_bundle_source_label(bundle.source_group)}",
            f"{style_text('Bundle 路径', Ansi.DIM)}: {bundle.bundle_dir}",
            f"{style_text('Rollout 相对路径', Ansi.DIM)} : {bundle.relative_path or '（空）'}",
            f"{style_text('会话类型', Ansi.DIM)}  : {bundle.session_kind or '（空）'}",
            f"{style_text('导出时间', Ansi.DIM)}  : {bundle.exported_at or '（空）'}",
            f"{style_text('更新时间', Ansi.DIM)}  : {bundle.updated_at or '（空）'}",
            f"{style_text('工作目录', Ansi.DIM)}  : {bundle.session_cwd or '（空）'}",
            f"{style_text('标题预览', Ansi.DIM)}  : {bundle.thread_name or '（空）'}",
        ]

    def _prompt_value(
        self,
        *,
        title: str,
        prompt_label: str,
        help_lines: List[str],
        default: str = "",
        allow_empty: bool = True,
    ) -> Optional[str]:
        box_width = self._print_branded_header(title)
        for line in render_box(help_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        suffix = f"（默认：{default}）" if default else ""
        raw = input(style_text(f"{prompt_label}{suffix}：", Ansi.BOLD, Ansi.CYAN)).strip()
        if not raw:
            if default:
                return default
            if allow_empty:
                return ""
            return None
        return raw

    def _confirm_toggle(
        self,
        *,
        title: str,
        question: str,
        yes_label: str,
        no_label: str,
        default_yes: bool = False,
    ) -> bool:
        default_hint = yes_label if default_yes else no_label
        answer = self._prompt_value(
            title=title,
            prompt_label=f"{question}（{yes_label}/{no_label}）",
            help_lines=[
                f"输入 {yes_label} 或 {no_label}。",
                f"直接回车默认选择：{default_hint}",
            ],
            default=yes_label if default_yes else no_label,
            allow_empty=False,
        )
        return str(answer).strip().lower() == yes_label.lower()

    def _show_detail_panel(
        self,
        title: str,
        lines: List[str],
        *,
        border_codes: Optional[Tuple[str, ...]] = None,
    ) -> None:
        box_width = self._print_branded_header(title)
        for line in render_box(lines, width=box_width, border_codes=border_codes or (Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")
        input(style_text("按 Enter 返回...", Ansi.DIM))

    def _run_session_clone_action(self, session_file: str, *, dry_run: bool) -> int:
        try:
            already_cloned = build_clone_index(self.paths, target_provider=self.context.target_provider, quiet=True)
            action, message, _ = clone_session_file(
                self.paths,
                Path(session_file),
                target_provider=self.context.target_provider,
                already_cloned_ids=already_cloned,
                dry_run=dry_run,
            )
        except ToolkitError as exc:
            action = "error"
            message = str(exc)

        color = Ansi.CYAN
        if action == "cloned":
            color = Ansi.GREEN if not dry_run else Ansi.YELLOW
        elif action == "error":
            color = Ansi.RED
        elif action.startswith("skipped"):
            color = Ansi.YELLOW

        print(style_text(f"结果：{action}", Ansi.BOLD, color))
        print(message)
        return 0 if action != "error" else 1

    def _session_action_center(self, summary: SessionSummary) -> None:
        pointer = glyphs().get("pointer", ">")
        actions = [
            {"key": "e", "label": "导出该会话为 Bundle", "color": Ansi.MAGENTA},
            {"key": "c", "label": "克隆到当前 provider", "color": Ansi.CYAN},
            {"key": "t", "label": "模拟克隆（Dry-run）", "color": Ansi.YELLOW},
            {"key": "q", "label": "返回", "color": Ansi.DIM},
        ]
        selected_index = 0

        while True:
            box_width = self._print_branded_header("会话详情 / 动作中心")
            for line in render_box(self._session_detail_lines(summary), width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
                print(line)
            print("")

            action_lines: List[str] = []
            for idx, action in enumerate(actions):
                label = f"[{action['key']}] {action['label']}"
                if idx == selected_index:
                    action_lines.append(style_text(f"{pointer} {label}", Ansi.BOLD, Ansi.UNDERLINE, action["color"]))
                else:
                    action_lines.append("  " + style_text(f"[{action['key']}]", Ansi.DIM, action["color"]) + f" {action['label']}")
            for line in render_box(action_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
                print(line)
            print("")
            print(style_text("按键：↑/↓ 选择 · Enter 执行 · e/c/t 快捷 · q 返回", Ansi.DIM))

            key = read_key()
            if key is None:
                raw = input("命令 [Enter/e/c/t/q]：").strip()
                key = raw if raw else "ENTER"

            if key in ("UP", "k", "K"):
                selected_index = (selected_index - 1) % len(actions)
                continue
            if key in ("DOWN", "j", "J"):
                selected_index = (selected_index + 1) % len(actions)
                continue

            action_key = actions[selected_index]["key"] if key == "ENTER" else str(key).strip().lower()
            if action_key in {"q", "esc", "0"} or key == "ESC":
                return
            if action_key == "e":
                self._run_action(
                    f"导出会话 {summary.session_id} 为 Bundle",
                    ["export", summary.session_id],
                    dry_run=False,
                    runner=lambda: self._run_toolkit(["export", summary.session_id]),
                    danger=False,
                )
                continue
            if action_key == "c":
                self._run_action(
                    f"克隆会话 {summary.session_id}",
                    ["clone-provider"],
                    dry_run=False,
                    runner=lambda path=str(summary.path): self._run_session_clone_action(path, dry_run=False),
                    danger=False,
                    preview_cmd=f"TUI selected-session clone -> {summary.session_id}",
                )
                continue
            if action_key == "t":
                self._run_action(
                    f"模拟克隆 {summary.session_id}",
                    ["clone-provider", "--dry-run"],
                    dry_run=True,
                    runner=lambda path=str(summary.path): self._run_session_clone_action(path, dry_run=True),
                    danger=False,
                    preview_cmd=f"TUI selected-session clone (dry-run) -> {summary.session_id}",
                )

    def _bundle_action_center(self, bundle: BundleSummary) -> None:
        pointer = glyphs().get("pointer", ">")
        actions = [
            {"key": "i", "label": "导入该 Bundle 为会话", "color": Ansi.GREEN},
            {"key": "v", "label": "导入该 Bundle 为会话并自动创建工作目录", "color": Ansi.CYAN},
            {"key": "q", "label": "返回", "color": Ansi.DIM},
        ]
        selected_index = 0

        while True:
            box_width = self._print_branded_header("Bundle 详情 / 动作中心")
            for line in render_box(self._bundle_detail_lines(bundle), width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
                print(line)
            print("")

            action_lines: List[str] = []
            for idx, action in enumerate(actions):
                label = f"[{action['key']}] {action['label']}"
                if idx == selected_index:
                    action_lines.append(style_text(f"{pointer} {label}", Ansi.BOLD, Ansi.UNDERLINE, action["color"]))
                else:
                    action_lines.append("  " + style_text(f"[{action['key']}]", Ansi.DIM, action["color"]) + f" {action['label']}")
            for line in render_box(action_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
                print(line)
            print("")
            print(style_text("按键：↑/↓ 选择 · Enter 执行 · i/v 快捷 · q 返回", Ansi.DIM))

            key = read_key()
            if key is None:
                raw = input("命令 [Enter/i/v/q]：").strip()
                key = raw if raw else "ENTER"

            if key in ("UP", "k", "K"):
                selected_index = (selected_index - 1) % len(actions)
                continue
            if key in ("DOWN", "j", "J"):
                selected_index = (selected_index + 1) % len(actions)
                continue

            action_key = actions[selected_index]["key"] if key == "ENTER" else str(key).strip().lower()
            if action_key in {"q", "esc", "0"} or key == "ESC":
                return
            if action_key == "i":
                self._run_action(
                    f"导入 Bundle {bundle.session_id} 为会话",
                    ["import", str(bundle.bundle_dir)],
                    dry_run=False,
                    runner=lambda: self._run_toolkit(["import", str(bundle.bundle_dir)]),
                    danger=False,
                )
                continue
            if action_key == "v":
                self._run_action(
                    f"导入 Bundle {bundle.session_id} 为会话（自动创建目录）",
                    ["import", "--desktop-visible", str(bundle.bundle_dir)],
                    dry_run=False,
                    runner=lambda: self._run_toolkit(["import", "--desktop-visible", str(bundle.bundle_dir)]),
                    danger=False,
                )

    def _open_session_browser(self, *, mode: str) -> Optional[SessionSummary]:
        filter_text = ""
        selected_index = 0
        pointer = glyphs().get("pointer", ">")

        while True:
            try:
                entries = collect_session_summaries(self.paths, pattern=filter_text, limit=200)
            except ToolkitError as exc:
                self._show_detail_panel("读取会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return None

            selected_index = max(0, min(selected_index, len(entries) - 1)) if entries else 0
            box_width = self._print_branded_header(
                "浏览本机会话" if mode == "view" else "选择要导出的会话",
                "↑/↓ 选择 · Enter 打开/确认 · / 过滤 · e 导出 · c 克隆 · t 模拟 · d 详情 · q 返回",
            )

            info_lines = [
                f"{style_text('过滤词', Ansi.DIM)} : {filter_text or '（无）'}",
                f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
                f"{style_text('模式', Ansi.DIM)}   : {'浏览 / 直接操作' if mode == 'view' else '选择后导出'}",
            ]
            for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
                print(line)
            print("")

            list_lines: List[str] = []
            if not entries:
                list_lines.append("没有匹配会话。按 / 修改过滤词，或按 q 返回。")
            else:
                start = max(0, selected_index - 5)
                start = min(start, max(0, len(entries) - 10))
                end = min(len(entries), start + 10)
                for idx in range(start, end):
                    summary = entries[idx]
                    preview = summary.preview or summary.path.name
                    line = (
                        f"{pointer if idx == selected_index else ' '} "
                        f"{summary.session_id} | {summary.kind}/{summary.scope} | "
                        f"{summary.model_provider or '-'} | {preview}"
                    )
                    if idx == selected_index:
                        list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                        if summary.cwd:
                            list_lines.append("  " + style_text(ellipsize_middle(summary.cwd, max(10, box_width - 10)), Ansi.DIM))
                    else:
                        list_lines.append(line)
            for line in render_box(list_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
                print(line)

            key = read_key()
            if key is None:
                raw = input("命令 [Enter/\\/e/c/t/d/q]：").strip()
                key = raw if raw else "ENTER"

            if key in ("UP", "k", "K"):
                if entries:
                    selected_index = (selected_index - 1) % len(entries)
                continue
            if key in ("DOWN", "j", "J"):
                if entries:
                    selected_index = (selected_index + 1) % len(entries)
                continue

            if key == "ENTER":
                if not entries:
                    continue
                selected = entries[selected_index]
                if mode == "view":
                    self._session_action_center(selected)
                    continue
                return selected

            key_str = str(key).strip().lower()
            if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
                return None
            if key_str in {"/", "f"}:
                new_filter = self._prompt_value(
                    title="浏览本机会话" if mode == "view" else "选择要导出的会话",
                    prompt_label="输入过滤词",
                    help_lines=[
                        "可按 session_id / provider / 路径 / cwd / 预览文本过滤。",
                        "留空表示不过滤。",
                    ],
                    allow_empty=True,
                )
                filter_text = new_filter or ""
                selected_index = 0
                continue
            if key_str == "e" and entries:
                selected = entries[selected_index]
                self._run_action(
                    f"导出会话 {selected.session_id} 为 Bundle",
                    ["export", selected.session_id],
                    dry_run=False,
                    runner=lambda sid=selected.session_id: self._run_toolkit(["export", sid]),
                    danger=False,
                )
                continue
            if key_str == "c" and entries:
                selected = entries[selected_index]
                self._run_action(
                    f"克隆会话 {selected.session_id}",
                    ["clone-provider"],
                    dry_run=False,
                    runner=lambda path=str(selected.path): self._run_session_clone_action(path, dry_run=False),
                    danger=False,
                    preview_cmd=f"TUI selected-session clone -> {selected.session_id}",
                )
                continue
            if key_str == "t" and entries:
                selected = entries[selected_index]
                self._run_action(
                    f"模拟克隆 {selected.session_id}",
                    ["clone-provider", "--dry-run"],
                    dry_run=True,
                    runner=lambda path=str(selected.path): self._run_session_clone_action(path, dry_run=True),
                    danger=False,
                    preview_cmd=f"TUI selected-session clone (dry-run) -> {selected.session_id}",
                )
                continue
            if key_str in {"d", " "} and entries:
                selected = entries[selected_index]
                if mode == "view":
                    self._session_action_center(selected)
                else:
                    self._show_detail_panel("会话详情", self._session_detail_lines(selected))

    def _open_bundle_browser(self, *, mode: str) -> Optional[BundleSummary]:
        filter_text = ""
        selected_index = 0
        source_group = "all"
        pointer = glyphs().get("pointer", ">")

        while True:
            try:
                entries = collect_known_bundle_summaries(
                    self.paths,
                    pattern=filter_text,
                    limit=240,
                    source_group=source_group,
                )
            except ToolkitError as exc:
                self._show_detail_panel("读取 Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return None

            selected_index = max(0, min(selected_index, len(entries) - 1)) if entries else 0
            box_width = self._print_branded_header(
                "浏览 Bundle 仓库" if mode == "view" else "选择要导入的 Bundle",
                "↑/↓ 选择 · Enter 打开/确认 · / 过滤 · s 切换来源 · i 导入 · v 自动建目录 · d 详情 · q 返回",
            )

            info_lines = [
                f"{style_text('过滤词', Ansi.DIM)} : {filter_text or '（无）'}",
                f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
                f"{style_text('来源筛选', Ansi.DIM)} : {format_bundle_source_label(source_group)}",
                f"{style_text('仓库根目录', Ansi.DIM)} : {self.context.bundle_root_label} + {self.context.desktop_bundle_root_label}",
            ]
            for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
                print(line)
            print("")

            list_lines: List[str] = []
            if not entries:
                list_lines.append("没有匹配 Bundle。按 / 修改过滤词，按 s 切换来源，或按 q 返回。")
            else:
                start = max(0, selected_index - 5)
                start = min(start, max(0, len(entries) - 10))
                end = min(len(entries), start + 10)
                for idx in range(start, end):
                    bundle = entries[idx]
                    title_text = bundle.thread_name or bundle.relative_path or bundle.session_id
                    source_label = format_bundle_source_label(bundle.source_group)
                    line = (
                        f"{pointer if idx == selected_index else ' '} "
                        f"{bundle.session_id} | {source_label} | "
                        f"{bundle.session_kind or '-'} | {title_text}"
                    )
                    if idx == selected_index:
                        list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                        list_lines.append("  " + style_text(ellipsize_middle(str(bundle.bundle_dir), max(10, box_width - 10)), Ansi.DIM))
                    else:
                        list_lines.append(line)
            for line in render_box(list_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
                print(line)

            key = read_key()
            if key is None:
                raw = input("命令 [Enter/\\/s/i/v/d/q]：").strip()
                key = raw if raw else "ENTER"

            if key in ("UP", "k", "K"):
                if entries:
                    selected_index = (selected_index - 1) % len(entries)
                continue
            if key in ("DOWN", "j", "J"):
                if entries:
                    selected_index = (selected_index + 1) % len(entries)
                continue

            if key == "ENTER":
                if not entries:
                    continue
                selected = entries[selected_index]
                if mode == "view":
                    self._bundle_action_center(selected)
                    continue
                return selected

            key_str = str(key).strip().lower()
            if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
                return None
            if key_str in {"/", "f"}:
                new_filter = self._prompt_value(
                    title="浏览 Bundle 仓库" if mode == "view" else "选择要导入的 Bundle",
                    prompt_label="输入过滤词",
                    help_lines=[
                        "可按 session_id / thread_name / kind / cwd / 路径过滤。",
                        "留空表示不过滤。",
                    ],
                    allow_empty=True,
                )
                filter_text = new_filter or ""
                selected_index = 0
                continue
            if key_str == "s":
                source_group = {
                    "all": "bundle",
                    "bundle": "desktop",
                    "desktop": "all",
                }[source_group]
                selected_index = 0
                continue
            if key_str == "i" and entries:
                bundle = entries[selected_index]
                self._run_action(
                    f"导入 Bundle {bundle.session_id} 为会话",
                    ["import", str(bundle.bundle_dir)],
                    dry_run=False,
                    runner=lambda path=str(bundle.bundle_dir): self._run_toolkit(["import", path]),
                    danger=False,
                )
                continue
            if key_str == "v" and entries:
                bundle = entries[selected_index]
                self._run_action(
                    f"导入 Bundle {bundle.session_id} 为会话（自动创建目录）",
                    ["import", "--desktop-visible", str(bundle.bundle_dir)],
                    dry_run=False,
                    runner=lambda path=str(bundle.bundle_dir): self._run_toolkit(["import", "--desktop-visible", path]),
                    danger=False,
                )
                continue
            if key_str in {"d", " "} and entries:
                bundle = entries[selected_index]
                if mode == "view":
                    self._bundle_action_center(bundle)
                else:
                    self._show_detail_panel("Bundle 详情", self._bundle_detail_lines(bundle), border_codes=(Ansi.DIM, Ansi.GREEN))

    def _resolve_menu_action_request(self, menu_action: TuiMenuAction) -> Tuple[Optional[str], Optional[List[str]]]:
        action_name = menu_action.label
        cli_args = list(menu_action.cli_args)

        if menu_action.action_id == "list_sessions":
            self._open_session_browser(mode="view")
            return None, None

        if menu_action.action_id == "browse_bundles":
            self._open_bundle_browser(mode="view")
            return None, None

        if menu_action.action_id == "export_one":
            summary = self._open_session_browser(mode="select")
            if not summary:
                return None, None
            return f"导出会话 {summary.session_id} 为 Bundle", ["export", summary.session_id]

        if menu_action.action_id == "import_one":
            bundle = self._open_bundle_browser(mode="select")
            if not bundle:
                return None, None
            desktop_visible = self._confirm_toggle(
                title="导入单个 Bundle 为会话",
                question="如果工作目录缺失，是否自动创建",
                yes_label="y",
                no_label="n",
                default_yes=False,
            )
            args = ["import"]
            if desktop_visible:
                args.append("--desktop-visible")
            args.append(str(bundle.bundle_dir))
            action_name = f"导入 Bundle {bundle.session_id} 为会话"
            if desktop_visible:
                action_name += "（自动创建目录）"
            return action_name, args

        if menu_action.action_id == "import_desktop_all":
            desktop_visible = self._confirm_toggle(
                title="批量导入全部 Desktop Bundle 为会话",
                question="如果工作目录缺失，是否自动创建",
                yes_label="y",
                no_label="n",
                default_yes=False,
            )
            args = ["import-desktop-all"]
            if desktop_visible:
                args.append("--desktop-visible")
            action_name = menu_action.label
            if desktop_visible:
                action_name += "（自动创建目录）"
            return action_name, args

        return action_name, cli_args

    def _tui_help_text(self) -> None:
        box_width = self._print_branded_header("帮助 / 使用说明")
        lines = [
            style_text("菜单分组：", Ansi.BOLD),
            "  Provider / Clone   : provider clone 与旧副本清理",
            "  Browse / Bundle    : 浏览会话仓库、Bundle 仓库、会话导出为 Bundle 与 Bundle 导入为会话",
            "  Desktop Repair     : 修复 provider / index / threads / workspace roots",
            "",
            style_text("常用 CLI（更完整的工具链能力）：", Ansi.BOLD),
            "  clone-provider                克隆活动会话到当前 provider",
            "  clean-clones                  清理旧版无标记副本",
            "  list [pattern]                列出本机会话",
            "  list-bundles [pattern]        列出 Bundle 仓库",
            "  validate-bundles              校验 Bundle 仓库健康度",
            "  export <session_id>           导出单个会话为 Bundle",
            "  export-desktop-all            批量导出全部 Desktop 会话为 Bundle（含 archived）",
            "  export-active-desktop-all     批量导出全部 Active Desktop 会话为 Bundle",
            "  export-cli-all                批量导出全部 CLI 会话为 Bundle",
            "  import <session_id|bundle_dir> 导入单个 Bundle 为会话",
            "  import-desktop-all            批量导入全部 Desktop Bundle 为会话",
            "  repair-desktop                修复 Desktop 左侧线程可见性",
            "",
            style_text("兼容参数（原 cloner 用法仍可继续使用）：", Ansi.BOLD),
            "  --dry-run          模拟运行（不写入/不删除）",
            "  --clean            清理旧版无标记副本（删除）",
            "  --no-tui           即使无参数也不进菜单（直接执行克隆）",
            "",
            style_text("示例：", Ansi.BOLD),
            f"  {self._cli_preview(('clone-provider', '--dry-run'))}",
            f"  {self._cli_preview(('list-bundles', '--source', 'desktop'))}",
            f"  {self._cli_preview(('validate-bundles', '--source', 'desktop'))}",
            f"  {self._cli_preview(('export-cli-all', '--dry-run'))}",
            f"  {self._cli_preview(('export', '019d582f-e8f4-7ce3-9948-c0406b4faaf2'))}",
            f"  {self._cli_preview(('import-desktop-all',))}",
            f"  {self._cli_preview(('repair-desktop', '--dry-run'))}",
            "",
            style_text("终端兼容：", Ansi.BOLD),
            "  NO_COLOR=1         关闭颜色输出",
            "  CSC_ASCII_UI=1     强制使用 ASCII 边框（不支持 Unicode 时可用）",
            "  CSC_TUI_MAX_WIDTH= 限制 TUI 最大宽度（用于超宽终端）",
            "",
            style_text("TUI 结构：", Ansi.BOLD),
            "  首页先选择功能域，再回车进入该功能页。",
            "  功能页内部再选择具体动作执行。",
            "",
            style_text("TUI 快捷键：", Ansi.BOLD),
            "  首页：↑/↓ 选择功能域，Enter 进入，q 退出",
            "  功能页：↑/↓ 选择动作，Enter 执行，q / ← 返回首页",
            "  功能页：←/→ 或 PgUp/PgDn 切换上一个 / 下一个功能页",
            "  h                  打开帮助",
            "  0                  直接退出",
            "",
            style_text("浏览器说明：", Ansi.BOLD),
            "  /                  在会话列表 / Bundle 列表中过滤",
            "  d                  打开详情 / 动作中心",
            "  e / c / t          在会话列表直接导出为 Bundle / 克隆 / 模拟克隆",
            "  s                  在 Bundle 列表切换来源过滤",
            "  i / v              在 Bundle 列表直接导入为会话 / 导入为会话并自动建目录",
        ]
        for line in render_box(lines, width=box_width, border_codes=(Ansi.DIM,)):
            print(line)
        print("")
        input("按 Enter 返回菜单...")

    def _render_home(self, selected_section_index: int) -> None:
        box_width, center = self._screen_layout()
        pointer = glyphs().get("pointer", ">")
        output_lines: List[str] = []
        selected_section = self.menu_sections[selected_section_index]

        for line in app_logo_lines(max_width=FIXED_THEME_LOGO_WIDTH):
            output_lines.append(align_line(line, box_width, center=center))
        output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
        output_lines.append(align_line(style_text("选择一个功能域，回车进入对应功能页", Ansi.DIM), box_width, center=center))
        output_lines.append(align_line(self._section_tabs_line(selected_section_index, box_width), box_width, center=center))
        output_lines.append("")

        info_lines = [
            f"{style_text('Provider', Ansi.DIM)} : {style_text(self.context.target_provider, Ansi.BOLD, Ansi.CYAN)}"
            f"  {style_text('Sessions', Ansi.DIM)} : {ellipsize_middle(self.context.active_sessions_dir, max(16, box_width - 40))}",
            f"{style_text('Config', Ansi.DIM)} : {ellipsize_middle(self.context.config_path, max(16, box_width - 18))}",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            output_lines.append(line)
        output_lines.append("")

        section_nav_lines = [style_text("功能域导航", Ansi.BOLD)]
        for pos, menu_section in enumerate(self.menu_sections):
            section_color = self._section_color(menu_section)
            header = f"[{pos + 1}] {menu_section.title}"
            if pos == selected_section_index:
                section_nav_lines.append(style_text(f"{pointer} {header}", Ansi.BOLD, Ansi.UNDERLINE, section_color))
            else:
                section_nav_lines.append("  " + style_text(header, Ansi.DIM, section_color))
        for line in render_box(section_nav_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            output_lines.append(line)
        output_lines.append("")

        selected_actions = self._actions_for_section(selected_section.section_id)
        preview_labels = " / ".join(action.label for _, action in selected_actions[:3])
        if len(selected_actions) > 3:
            preview_labels += " / ..."
        summary_lines = [
            style_text(selected_section.title, Ansi.BOLD, self._section_color(selected_section)),
            f"{style_text('动作数', Ansi.DIM)} : {len(selected_actions)}",
        ]
        for note in self._section_notes(selected_section)[:1]:
            summary_lines.append(f"{style_text('说明', Ansi.DIM)} : {note}")
        summary_lines.append(f"{style_text('包含动作', Ansi.DIM)} : {preview_labels}")
        for line in render_box(summary_lines, width=box_width, border_codes=selected_section.border_codes):
            output_lines.append(line)
        output_lines.append("")

        output_lines.append(style_text("Enter 进入功能页  |  ↑/↓ 选择功能域  |  h 帮助  |  q 退出", Ansi.DIM))
        if os.name == "nt":
            output_lines.append(style_text(f"提示：先运行 .\\install.ps1，再用 .\\{self.context.entry_command}.cmd 启动", Ansi.DIM))
        else:
            output_lines.append(style_text(f"提示：先运行 ./install.sh，再用 ./{self.context.entry_command} 启动", Ansi.DIM))

        hide_cursor = "\033[?25l"
        show_cursor = "\033[?25h"
        home_cursor = "\033[H"
        clear_to_eol = "\033[K"
        clear_to_eos = "\033[J"
        visible_lines = self._fit_lines_to_screen(output_lines)
        full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
        sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
        sys.stdout.flush()

    def _render_section_page(self, section_index: int, action_offset: int) -> None:
        box_width, center = self._screen_layout()
        screen_height = self._screen_height()
        pointer = glyphs().get("pointer", ">")
        output_lines: List[str] = []

        menu_section = self.menu_sections[section_index]
        section_actions = self._actions_for_section(menu_section.section_id)
        if not section_actions:
            return

        action_offset = max(0, min(action_offset, len(section_actions) - 1))
        selected_index, selected_action = section_actions[action_offset]
        for line in app_logo_lines(max_width=FIXED_THEME_LOGO_WIDTH):
            output_lines.append(align_line(line, box_width, center=center))
        output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
        output_lines.append(align_line(style_text(f"{menu_section.title} / 功能页", Ansi.DIM), box_width, center=center))
        output_lines.append(align_line(self._section_tabs_line(section_index, box_width), box_width, center=center))
        output_lines.append("")

        info_lines = [
            f"{style_text('当前动作', Ansi.DIM)} : {style_text(selected_action.label, Ansi.BOLD, self._action_color(selected_action))}",
            f"{style_text('执行方式', Ansi.DIM)} : 直接在 TUI 中执行",
            f"{style_text('目标 Provider', Ansi.DIM)} : {style_text(self.context.target_provider, Ansi.BOLD, Ansi.CYAN)}",
        ]
        for note in self._action_notes(selected_action)[:1]:
            info_lines.append(f"{style_text('说明', Ansi.DIM)} : {note}")
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            output_lines.append(line)
        output_lines.append("")

        section_lines = [style_text(menu_section.title, Ansi.BOLD)]
        reserved_rows = len(output_lines) + 2
        max_visible_actions = max(3, screen_height - reserved_rows - 4)
        start, end = self._action_window(len(section_actions), action_offset, max_visible_actions)
        if start > 0:
            section_lines.append(style_text("... 上方还有更多动作 ...", Ansi.DIM))
        for offset in range(start, end):
            _, menu_action = section_actions[offset]
            hotkey = f"[{menu_action.hotkey}]"
            label = f"{hotkey} {menu_action.label}"
            if offset == action_offset:
                prefix = style_text(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
                section_lines.append(prefix + style_text(label, Ansi.BOLD, Ansi.UNDERLINE, self._action_color(menu_action)))
            else:
                section_lines.append("  " + style_text(hotkey, Ansi.DIM, self._action_color(menu_action)) + " " + menu_action.label)
        if end < len(section_actions):
            section_lines.append(style_text("... 下方还有更多动作 ...", Ansi.DIM))
        for line in render_box(section_lines, width=box_width, border_codes=menu_section.border_codes):
            output_lines.append(line)
        output_lines.append("")

        output_lines.append(style_text("↑/↓ 选择动作  |  Enter 执行  |  ←/q 返回首页  |  →/PgDn 下一功能页  |  PgUp 上一功能页", Ansi.DIM))

        hide_cursor = "\033[?25l"
        show_cursor = "\033[?25h"
        home_cursor = "\033[H"
        clear_to_eol = "\033[K"
        clear_to_eos = "\033[J"
        visible_lines = self._fit_lines_to_screen(output_lines)
        full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
        sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
        sys.stdout.flush()

    def _execute_menu_action(self, chosen_action: TuiMenuAction) -> None:
        choice_id = chosen_action.action_id
        if choice_id == "clone":
            self._run_action(
                "克隆会话（幂等）",
                chosen_action.cli_args,
                dry_run=False,
                runner=lambda: run_clone_mode(target_provider=self.context.target_provider, dry_run=False),
                danger=False,
            )
            return
        if choice_id == "clone_dry":
            self._run_action(
                "模拟克隆（Dry-run）",
                chosen_action.cli_args,
                dry_run=True,
                runner=lambda: run_clone_mode(target_provider=self.context.target_provider, dry_run=True),
                danger=False,
            )
            return
        if choice_id == "clean":
            if not self._confirm_dangerous_action(chosen_action.cli_args):
                return
            self._run_action(
                "清理旧版无标记副本（删除）",
                chosen_action.cli_args,
                dry_run=False,
                runner=lambda: run_cleanup_mode(target_provider=self.context.target_provider, dry_run=False),
                danger=True,
            )
            return
        if choice_id == "clean_dry":
            self._run_action(
                "模拟清理（Dry-run）",
                chosen_action.cli_args,
                dry_run=True,
                runner=lambda: run_cleanup_mode(target_provider=self.context.target_provider, dry_run=True),
                danger=True,
            )
            return

        action_name, cli_args = self._resolve_menu_action_request(chosen_action)
        if cli_args is not None:
            self._run_action(
                action_name or chosen_action.label,
                cli_args,
                dry_run=chosen_action.is_dry_run,
                runner=lambda args=cli_args: self._run_toolkit(args),
                danger=chosen_action.is_dangerous,
            )

    def _run_action(
        self,
        action_name: str,
        cli_args: Sequence[str],
        *,
        dry_run: bool,
        runner: Callable[[], int],
        danger: bool,
        preview_cmd: Optional[str] = None,
    ) -> None:
        box_width = self._print_branded_header("执行中…")
        color = Ansi.RED if danger and not dry_run else Ansi.YELLOW if dry_run else Ansi.CYAN
        print(style_text(f"▶ {action_name}", Ansi.BOLD, color))
        print("")

        info_lines = [
            f"{style_text('执行方式', Ansi.DIM)}  : 直接在 TUI 中执行",
            f"{style_text('当前动作', Ansi.DIM)}  : {style_text(action_name, Ansi.BOLD, color)}",
            f"{style_text('目标 Provider', Ansi.DIM)} : {style_text(self.context.target_provider, Ansi.BOLD, Ansi.CYAN)}",
            f"{style_text('会话目录', Ansi.DIM)}      : {style_text(self.context.active_sessions_dir, Ansi.DIM)}",
        ]
        if danger and not dry_run:
            info_lines.append(style_text("【危险】", Ansi.BOLD, Ansi.RED) + "将删除文件，无法恢复。")
        elif dry_run:
            info_lines.append(style_text("【DRY-RUN】", Ansi.BOLD, Ansi.YELLOW) + "不写入/不删除。")
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        result = runner()
        if result != 0:
            print(style_text(f"\n操作返回状态码：{result}", Ansi.BOLD, Ansi.YELLOW))
        input(style_text("\n按 Enter 返回菜单...", Ansi.DIM))

    def _confirm_dangerous_action(self, cli_args: Sequence[str]) -> bool:
        box_width = self._print_branded_header("危险操作确认", "该操作会删除文件，且无法恢复。")
        info_lines = [
            style_text("【危险】", Ansi.BOLD, Ansi.RED) + "Clean 会删除旧版无标记副本文件。",
            f"{style_text('执行方式', Ansi.DIM)} : 直接在 TUI 中执行",
            f"{style_text('影响范围', Ansi.DIM)} : 旧版无标记 clone 文件",
            "",
            "确认方式：输入 DELETE 并回车。",
            "取消方式：直接回车。",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.RED)):
            print(line)
        print("")
        return input(style_text("请输入 DELETE 确认执行：", Ansi.BOLD, Ansi.RED)).strip() == "DELETE"

    def run(self) -> int:
        selected_section = 0
        current_view = "home"
        section_action_offsets = {
            menu_section.section_id: 0
            for menu_section in self.menu_sections
        }
        last_size = (term_width(), term_height())
        sys.stdout.write("\033[?1049h\033[H")
        sys.stdout.flush()
        try:
            clear_screen()
            while True:
                if current_view == "home":
                    self._render_home(selected_section)
                else:
                    current_section = self.menu_sections[selected_section]
                    current_offset = section_action_offsets[current_section.section_id]
                    self._render_section_page(selected_section, current_offset)

                key = read_key(timeout_ms=200)
                current_size = (term_width(), term_height())
                if current_size != last_size:
                    last_size = current_size
                    continue
                if key is None:
                    continue

                if current_view == "home":
                    if key in ("UP", "k", "K"):
                        selected_section = (selected_section - 1) % len(self.menu_sections)
                        continue
                    if key in ("DOWN", "j", "J"):
                        selected_section = (selected_section + 1) % len(self.menu_sections)
                        continue
                    if key in ("LEFT", "PAGE_UP"):
                        selected_section = (selected_section - 1) % len(self.menu_sections)
                        continue
                    if key in ("RIGHT", "PAGE_DOWN"):
                        selected_section = (selected_section + 1) % len(self.menu_sections)
                        continue

                    if key == "ENTER":
                        current_view = "section"
                        continue

                    key_str = str(key).strip().lower()
                    if key_str in {"q", "quit", "exit", "0"}:
                        return 0
                    if key_str in {"h", "help", "?"}:
                        clear_screen()
                        self._tui_help_text()
                        continue
                    if key_str in {"1", "2", "3"}:
                        selected_section = min(len(self.menu_sections) - 1, int(key_str) - 1)
                        current_view = "section"
                        continue
                    continue

                current_section = self.menu_sections[selected_section]
                section_actions = self._actions_for_section(current_section.section_id)
                if not section_actions:
                    current_view = "home"
                    continue

                current_offset = max(0, min(section_action_offsets[current_section.section_id], len(section_actions) - 1))
                section_action_offsets[current_section.section_id] = current_offset

                if key in ("UP", "k", "K"):
                    section_action_offsets[current_section.section_id] = (current_offset - 1) % len(section_actions)
                    continue
                if key in ("DOWN", "j", "J"):
                    section_action_offsets[current_section.section_id] = (current_offset + 1) % len(section_actions)
                    continue
                if key in ("LEFT", "PAGE_UP"):
                    if key == "LEFT":
                        current_view = "home"
                        continue
                    selected_section = (selected_section - 1) % len(self.menu_sections)
                    continue
                if key in ("RIGHT", "PAGE_DOWN"):
                    selected_section = (selected_section + 1) % len(self.menu_sections)
                    continue

                selected_action = section_actions[current_offset][1]
                if key == "ENTER":
                    self._execute_menu_action(selected_action)
                    continue

                key_str = str(key).strip().lower()
                if key_str in {"q", "esc", "b", "back"} or key == "ESC":
                    current_view = "home"
                    continue
                if key_str == "0":
                    return 0
                if key_str in {"h", "help", "?"}:
                    clear_screen()
                    self._tui_help_text()
                    continue

                matched_action = None
                for _, menu_action in section_actions:
                    if menu_action.hotkey == key_str:
                        matched_action = menu_action
                        break
                if matched_action is not None:
                    self._execute_menu_action(matched_action)
        finally:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()


def run_tui(context: ToolkitAppContext) -> int:
    return ToolkitTuiApp(context).run()
