"""Interactive TUI for cc-clean."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set

from ..models import ExecutionSummary, PlanItem, RunOptions
from ..paths import ClaudePaths
from ..services import FULL_TARGET_KEYS, SAFE_TARGET_KEYS, build_plan, execute_plan, format_bytes
from ...core.tui.screen_mode import ScreenModeDecision, resolve_screen_mode
from .terminal import (
    Ansi,
    align_line,
    app_logo_lines,
    ellipsize_middle,
    glyphs,
    read_key,
    render_box,
    style_text,
    term_height,
    term_width,
    tui_width,
)


@dataclass
class TuiState:
    selected_keys: Set[str]
    backup_enabled: bool = True
    dry_run: bool = False
    cursor_index: int = 0
    flash_message: str = ""


class CleanerTuiApp:
    def __init__(self, paths: ClaudePaths, screen_mode: Optional[ScreenModeDecision] = None):
        self.paths = paths
        self.state = TuiState(selected_keys=set(SAFE_TARGET_KEYS))
        self.screen_mode = screen_mode or resolve_screen_mode()
        self._last_frame = ""
        self._last_lines: List[str] = []

    def run(self) -> int:
        import os
        import signal

        last_size = (term_width(), term_height())
        plan_dirty = True
        frame_dirty = True
        plan: Sequence[PlanItem] = build_plan(self.paths, self.state.selected_keys)

        # SIGWINCH (Unix only) wakes the blocking read so resize redraws fire
        # immediately rather than waiting for the next keystroke. On Windows
        # there is no SIGWINCH, so we fall back to a 500ms polling timeout —
        # the existing frame_dirty / _paint_incremental machinery means
        # idle frames cost almost nothing visually.
        resize_pending = {"flag": False}
        prev_handler = None
        try:
            sigwinch = getattr(signal, "SIGWINCH", None)
            if sigwinch is not None:
                def _on_winch(signum, frame):  # noqa: ARG001
                    resize_pending["flag"] = True
                prev_handler = signal.signal(sigwinch, _on_winch)
        except (ValueError, OSError):
            prev_handler = None

        poll_timeout_ms = 500 if os.name == "nt" else None

        self._enter_terminal()
        try:
            while True:
                current_size = (term_width(), term_height())
                if resize_pending["flag"] or current_size != last_size:
                    resize_pending["flag"] = False
                    last_size = current_size
                    frame_dirty = True

                if plan_dirty:
                    plan = build_plan(self.paths, self.state.selected_keys)
                    plan_dirty = False
                    frame_dirty = True

                if frame_dirty:
                    self._paint_frame(self._home_frame(plan))
                    frame_dirty = False

                key = read_key(timeout_ms=poll_timeout_ms)
                if key is None:
                    continue
                if key in {"q", "Q", "ESC"}:
                    return 0

                if key == "UP":
                    self.state.cursor_index = max(0, self.state.cursor_index - 1)
                    frame_dirty = True
                    continue
                if key == "DOWN":
                    self.state.cursor_index = min(len(plan) - 1, self.state.cursor_index + 1)
                    frame_dirty = True
                    continue
                if key in {" ", "ENTER"}:
                    self._toggle_index(self.state.cursor_index, plan)
                    plan_dirty = True
                    continue
                if key.isdigit():
                    index = int(key) - 1
                    if 0 <= index < len(plan):
                        self.state.cursor_index = index
                        self._toggle_index(index, plan)
                        plan_dirty = True
                    continue

                lower = key.lower()
                if lower == "a":
                    self.state.selected_keys = set(SAFE_TARGET_KEYS)
                    self.state.flash_message = "已加载安全预设。"
                    plan_dirty = True
                    continue
                if lower == "f":
                    self.state.selected_keys = set(FULL_TARGET_KEYS)
                    self.state.flash_message = "已加载完整重置预设。"
                    plan_dirty = True
                    continue
                if lower == "n":
                    self.state.selected_keys = set()
                    self.state.flash_message = "已清空所有勾选项。"
                    plan_dirty = True
                    continue
                if lower == "b":
                    self.state.backup_enabled = not self.state.backup_enabled
                    if self.state.backup_enabled:
                        self.state.flash_message = "备份已开启；执行时会先移动到备份目录。"
                    else:
                        self.state.flash_message = "备份已关闭；执行时会直接删除，不代表该选项不可用。"
                    frame_dirty = True
                    continue
                if lower == "d":
                    self.state.dry_run = not self.state.dry_run
                    if self.state.dry_run:
                        self.state.flash_message = "演练模式已开启；这次只预览变更，不会实际写入。"
                    else:
                        self.state.flash_message = "演练模式已关闭；执行时会真正修改文件。"
                    frame_dirty = True
                    continue
                if lower == "r":
                    self._show_plan_review(plan)
                    frame_dirty = True
                    continue
                if lower == "x":
                    self._execute(plan)
                    plan_dirty = True
                    continue
        finally:
            self._leave_terminal()
            try:
                if prev_handler is not None and getattr(signal, "SIGWINCH", None) is not None:
                    signal.signal(signal.SIGWINCH, prev_handler)
            except (ValueError, OSError):
                pass

    def _enter_terminal(self) -> None:
        self._last_frame = ""
        self._last_lines = []
        # When invoked from the ``aik`` hub the parent already owns the alt
        # screen + cursor-hide state; toggling them again would briefly
        # surface the outer shell as a "flash" between hub → tool. The
        # ``AIK_HUB_ACTIVE`` flag lets us share one continuous alt-screen
        # surface across hub → tool → hub transitions.
        import os
        if os.environ.get("AIK_HUB_ACTIVE"):
            sys.stdout.write("\033[2J\033[H\033[?25l")
        else:
            sys.stdout.write(self.screen_mode.enter_sequence)
        sys.stdout.flush()

    def _leave_terminal(self) -> None:
        self._last_frame = ""
        self._last_lines = []
        import os
        if os.environ.get("AIK_HUB_ACTIVE"):
            # Hand control back to the hub on the same alt-screen surface;
            # just clear so the hub redraws on a clean canvas.
            sys.stdout.write("\033[2J\033[H")
        else:
            sys.stdout.write(self.screen_mode.exit_sequence)
        sys.stdout.flush()

    def _toggle_index(self, index: int, plan: Sequence[PlanItem]) -> None:
        key = plan[index].target.key
        if key in self.state.selected_keys:
            self.state.selected_keys.remove(key)
        else:
            self.state.selected_keys.add(key)
        self.state.flash_message = "已切换：%s。" % plan[index].target.label

    def _paint_frame(self, frame: str, *, force: bool = False) -> None:
        lines = self._frame_lines(frame)
        normalized_frame = "\n".join(lines)
        if not force and normalized_frame == self._last_frame:
            return
        if force or not self._last_lines:
            self._paint_full(lines)
        else:
            self._paint_incremental(lines)
        sys.stdout.flush()
        self._last_frame = normalized_frame
        self._last_lines = lines

    def _paint_full(self, lines: Sequence[str]) -> None:
        sys.stdout.write("\033[H\033[J")
        if lines:
            sys.stdout.write("\n".join(lines))
        sys.stdout.write("\n")

    def _paint_incremental(self, lines: Sequence[str]) -> None:
        old_lines = self._last_lines
        row_count = max(len(old_lines), len(lines))
        pending: List[str] = []
        for row_index in range(row_count):
            new_line = lines[row_index] if row_index < len(lines) else ""
            old_line = old_lines[row_index] if row_index < len(old_lines) else ""
            if new_line == old_line:
                continue
            pending.append("\033[%d;1H\033[2K%s" % (row_index + 1, new_line))
        if len(lines) < len(old_lines):
            pending.append("\033[%d;1H\033[J" % (len(lines) + 1))
        if pending:
            sys.stdout.write("".join(pending))

    def _box_width(self) -> int:
        return min(tui_width(), 98)

    def _screen_height(self) -> int:
        return max(12, term_height())

    def _fit_lines_to_screen(self, lines: Sequence[str]) -> List[str]:
        max_rows = self._screen_height()
        visible = list(lines)
        if len(visible) <= max_rows:
            return visible

        trimmed = visible[: max(6, max_rows - 1)]
        trimmed[-1] = align_line(
            style_text("... 终端高度不足，下方内容已折叠 ...", Ansi.DIM, Ansi.YELLOW),
            tui_width(),
            center=True,
        )
        return trimmed

    def _frame_lines(self, frame: str) -> List[str]:
        return self._fit_lines_to_screen(frame.splitlines())

    def _compact_layout(self) -> bool:
        return self._screen_height() <= 28

    def _brand_header_lines(self, title: str, subtitle: str) -> List[str]:
        width = tui_width()
        box_width = self._box_width()
        logo_width = min(60, max(28, box_width - 8))
        lines: List[str] = []
        logo_lines = app_logo_lines(max_width=logo_width)
        if self._compact_layout() and logo_lines:
            logo_lines = [logo_lines[-1]]
        for line in logo_lines:
            lines.append(align_line(line, width, center=True))
        lines.append(align_line(style_text(title, Ansi.BOLD, Ansi.CYAN), width, center=True))
        lines.append(align_line(style_text(subtitle, Ansi.DIM), width, center=True))
        return lines

    def _screen_mode_text(self) -> str:
        mode_labels = {"main": "主屏", "alt": "副屏", "auto": "自动"}
        requested = mode_labels.get(self.screen_mode.requested, self.screen_mode.requested)
        resolved = mode_labels.get(self.screen_mode.resolved, self.screen_mode.resolved)
        if self.screen_mode.requested == self.screen_mode.resolved:
            return resolved
        return "%s→%s" % (requested, resolved)

    def _plan_status_text(self, item: PlanItem) -> str:
        if item.applicable:
            return "可执行"
        if item.exists:
            return "存在"
        return "缺失"

    def _risk_text(self, danger: bool) -> str:
        return "危险" if danger else "安全"

    def _record_status_text(self, status: str) -> str:
        return {
            "moved": "已备份",
            "deleted": "已删除",
            "updated": "已更新",
            "skipped": "已跳过",
            "dry-run": "演练",
            "error": "错误",
        }.get(status, status)

    def _visible_plan_lines(self, plan: Sequence[PlanItem], box_width: int) -> List[str]:
        pointer = glyphs().get("pointer", ">")
        lines: List[str] = [style_text("清理目标", Ansi.BOLD)]
        if not plan:
            lines.append(style_text("当前没有可清理目标。", Ansi.DIM))
            return lines

        height_budget = self._screen_height()
        compact = self._compact_layout()
        reserved_rows = 14 if compact else 20
        available_box_rows = max(8, height_budget - reserved_rows)
        max_content_rows = max(4, available_box_rows - 2)
        body_rows = max(2, max_content_rows - 1)
        reserve_indicator_rows = 2 if len(plan) > 1 else 0
        visible_items = max(1, (body_rows - reserve_indicator_rows) // 2)
        visible_items = min(len(plan), visible_items)

        start = max(0, self.state.cursor_index - (visible_items // 2))
        start = min(start, max(0, len(plan) - visible_items))
        end = min(len(plan), start + visible_items)

        if start > 0:
            lines.append(style_text("... 上方还有更多目标 ...", Ansi.DIM))

        for index in range(start, end):
            item = plan[index]
            is_selected = item.target.key in self.state.selected_keys
            row_prefix = pointer if index == self.state.cursor_index else " "
            checkbox = "[x]" if is_selected else "[ ]"
            status = self._plan_status_text(item)
            risk = self._risk_text(item.target.danger)
            size = format_bytes(item.size_bytes)
            label = "%d. %s %s" % (index + 1, checkbox, item.target.label)
            left_width = max(18, box_width - 34)
            summary_line = "%s %-*s  %-7s  %-7s  %8s" % (
                row_prefix,
                left_width,
                ellipsize_middle(label, left_width),
                status,
                risk,
                size,
            )
            row_color = Ansi.YELLOW if item.target.danger else (Ansi.GREEN if item.applicable else Ansi.DIM)
            detail_parts = [item.details]
            if item.warnings:
                detail_parts.extend("警告：%s" % warning for warning in item.warnings)
            detail_line = "  " + ellipsize_middle(" | ".join(detail_parts), max(10, box_width - 6))
            if index == self.state.cursor_index:
                lines.append(style_text(summary_line, Ansi.BOLD, row_color))
            else:
                lines.append(style_text(summary_line, row_color))
            lines.append(style_text(detail_line, Ansi.DIM))

        if end < len(plan):
            lines.append(style_text("... 下方还有更多目标 ...", Ansi.DIM))

        return lines

    def _home_frame(self, plan: Sequence[PlanItem]) -> str:
        width = tui_width()
        box_width = self._box_width()
        selected_count = len([item for item in plan if item.target.key in self.state.selected_keys])
        applicable_count = len([item for item in plan if item.selected and item.applicable])

        output_lines = self._brand_header_lines(
            "Claude 本地清理助手",
            "默认安全模式；危险项需要你手动勾选。",
        )
        output_lines.append("")

        header_lines = [
            "主目录：%s" % ellipsize_middle(str(self.paths.home), max(10, box_width - 10)),
            "已勾选：%d    当前可执行：%d" % (selected_count, applicable_count),
            "备份：%s    演练模式：%s"
            % ("开启" if self.state.backup_enabled else "关闭", "开启" if self.state.dry_run else "关闭"),
            "屏幕模式：%s" % self._screen_mode_text(),
        ]
        for line in render_box(header_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.CYAN)):
            output_lines.append(align_line(line, width, center=True))

        output_lines.append("")
        list_lines = self._visible_plan_lines(plan, box_width)
        for line in render_box(list_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            output_lines.append(align_line(line, width, center=True))

        output_lines.append("")
        if self._compact_layout():
            help_lines = [
                "方向键移动  |  Space 勾选  |  x 执行  |  q 退出",
                "a 安全预设  |  f 完整预设  |  n 清空  |  b 备份  |  d 演练  |  r 预览",
            ]
        else:
            help_lines = [
                "方向键移动  |  Space/Enter 勾选  |  1-9 按序号切换",
                "a 安全预设  |  f 完整重置  |  n 清空全部  |  b 备份  |  d 演练模式",
                "r 预览计划  |  x 执行  |  q 退出",
            ]
        for line in render_box(help_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            output_lines.append(align_line(line, width, center=True))

        if self.state.flash_message:
            output_lines.append("")
            flash_lines = [self.state.flash_message]
            for line in render_box(flash_lines, width=min(box_width, 72), border_codes=(Ansi.DIM, Ansi.BRIGHT_MAGENTA)):
                output_lines.append(align_line(line, width, center=True))

        return "\n".join(output_lines)

    def _modal_frame(
        self,
        *,
        title: str,
        subtitle: str,
        lines: Sequence[str],
        border_codes: Sequence[str],
        footer: str = "按任意键返回。",
    ) -> str:
        width = tui_width()
        box_width = self._box_width()
        output_lines = self._brand_header_lines(title, subtitle)
        output_lines.append("")
        for line in render_box(list(lines), width=box_width, border_codes=tuple(border_codes)):
            output_lines.append(align_line(line, width, center=True))
        if footer:
            output_lines.append("")
            output_lines.append(align_line(style_text(footer, Ansi.DIM), width, center=True))
        return "\n".join(output_lines)

    def _show_plan_review(self, plan: Sequence[PlanItem]) -> None:
        selected = [item for item in plan if item.target.key in self.state.selected_keys]
        lines: List[str] = []
        if not selected:
            lines.append("当前没有勾选任何目标。")
        for item in selected:
            prefix = "危险" if item.target.danger else "安全"
            lines.append("%s | %s" % (prefix, item.target.label))
            lines.append("  路径：%s" % item.target.target_path)
            lines.append("  说明：%s" % item.details)
            for warning in item.warnings:
                lines.append("  警告：%s" % warning)
        frame = self._modal_frame(
            title="已选计划",
            subtitle="执行前先确认当前勾选项。",
            lines=lines or ["当前没有勾选任何目标。"],
            border_codes=(Ansi.DIM, Ansi.BLUE),
        )
        self._paint_frame(frame, force=True)
        read_key()

    def _execute(self, plan: Sequence[PlanItem]) -> None:
        selected = [item for item in plan if item.target.key in self.state.selected_keys]
        if not selected:
            self.state.flash_message = "执行前至少勾选一个目标。"
            return

        if not self.state.dry_run and not self._confirm_execution(selected):
            self.state.flash_message = "已取消执行。"
            return

        summary = execute_plan(
            self.paths,
            build_plan(self.paths, self.state.selected_keys),
            RunOptions(
                backup_enabled=self.state.backup_enabled,
                dry_run=self.state.dry_run,
            ),
        )
        self._show_summary(summary)

    def _confirm_execution(self, selected: Sequence[PlanItem]) -> bool:
        lines = ["即将执行以下已勾选项目："]
        for item in selected:
            risk = "危险" if item.target.danger else "安全"
            lines.append("%s | %s" % (risk, item.target.label))
        lines.append("")
        lines.append("备份：%s" % ("开启" if self.state.backup_enabled else "关闭"))
        lines.append("演练模式：%s" % ("开启" if self.state.dry_run else "关闭"))
        lines.append("")
        lines.append("按 y 继续，其他任意键取消。")

        frame = self._modal_frame(
            title="确认清理",
            subtitle="危险目标可能会删除旧的本地会话历史。",
            lines=lines,
            border_codes=(Ansi.DIM, Ansi.YELLOW),
            footer="按 y 继续，其他任意键取消。",
        )
        self._paint_frame(frame, force=True)
        key = read_key()
        return bool(key and key.lower() == "y")

    def _show_summary(self, summary: ExecutionSummary) -> None:
        lines: List[str] = []
        if summary.backup_root:
            lines.append("备份目录：%s" % summary.backup_root)
            lines.append("")
        if not summary.records:
            lines.append("没有应用任何变更。")
        for record in summary.records:
            lines.append("[%s] %s - %s" % (self._record_status_text(record.status), record.key, record.message))
            if record.backup_path:
                lines.append("  备份：%s" % record.backup_path)

        frame = self._modal_frame(
            title="执行摘要",
            subtitle="清理已完成，关闭前可检查备份路径。",
            lines=lines or ["没有应用任何变更。"],
            border_codes=(Ansi.DIM, Ansi.GREEN),
        )
        self._paint_frame(frame, force=True)
        read_key()


def run_tui(paths: ClaudePaths) -> int:
    return CleanerTuiApp(paths).run()
