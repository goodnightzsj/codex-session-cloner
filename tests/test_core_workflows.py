import json
import os
import shlex
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ai_cli_kit.codex.paths import CodexPaths  # noqa: E402
from ai_cli_kit.codex.models import BundleSummary  # noqa: E402
from ai_cli_kit.codex.services.browse import get_bundle_summaries, get_session_summaries, validate_bundles  # noqa: E402
from ai_cli_kit.codex.services.clone import clone_to_provider  # noqa: E402
from ai_cli_kit.codex.services.dedupe import dedupe_clones  # noqa: E402
from ai_cli_kit.codex.services.exporting import export_active_desktop_all, export_session  # noqa: E402
from ai_cli_kit.codex.services.importing import import_desktop_all, import_session  # noqa: E402
from ai_cli_kit.codex.services.repair import repair_desktop  # noqa: E402
from ai_cli_kit.codex.support import machine_label_to_key  # noqa: E402
from ai_cli_kit.codex.stores.bundles import collect_known_bundle_summaries, latest_distinct_bundle_summaries  # noqa: E402
from ai_cli_kit.codex.stores.index import load_existing_index  # noqa: E402
from ai_cli_kit.codex.stores.session_files import iter_session_files, read_session_payload  # noqa: E402
from ai_cli_kit.codex.validation import load_manifest, validate_relative_path  # noqa: E402


@contextmanager
def pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def env_override(key: str, value: str):
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def write_config(home: Path, provider: str) -> None:
    code_dir = home / ".codex"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "config.toml").write_text(f'model_provider = "{provider}"\n', encoding="utf-8")


def write_state_file(home: Path) -> None:
    state_file = home / ".codex" / ".codex-global-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "electron-saved-workspace-roots": [],
                "active-workspace-roots": [],
                "project-order": [],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def create_threads_db(home: Path) -> Path:
    db_path = home / ".codex" / "state_0001.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        create table threads (
            id text primary key,
            rollout_path text,
            created_at integer,
            updated_at integer,
            source text,
            model_provider text,
            cwd text,
            title text,
            sandbox_policy text,
            approval_mode text,
            tokens_used integer,
            has_user_event integer,
            archived integer,
            archived_at integer,
            cli_version text,
            first_user_message text,
            memory_mode text,
            model text,
            reasoning_effort text
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def write_history(home: Path, session_id: str, text: str) -> None:
    history_file = home / ".codex" / "history.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"session_id": session_id, "text": text}
    with history_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")) + "\n")


def write_session(
    home: Path,
    session_id: str,
    *,
    provider: str,
    source: str,
    originator: str,
    cwd: Path,
    archived: bool = False,
    timestamp: str = "2026-04-10T10:00:00Z",
    user_message: str = "",
    include_env_context: bool = False,
    cloned_from: str = "",
) -> Path:
    base = home / ".codex" / ("archived_sessions" if archived else "sessions") / "2026" / "04" / "10"
    base.mkdir(parents=True, exist_ok=True)
    rollout = base / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    payload = {
        "id": session_id,
        "model_provider": provider,
        "source": source,
        "originator": originator,
        "cwd": str(cwd),
        "timestamp": timestamp,
        "cli_version": "0.1.0",
    }
    if cloned_from:
        payload["cloned_from"] = cloned_from
    lines = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": payload,
        },
    ]
    if include_env_context:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:30Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>/tmp</cwd>\n</environment_context>"}],
                },
            }
        )
    if user_message:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:45Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_message}],
                },
            }
        )
    lines.extend(
        [
            {
                "timestamp": "2026-04-10T10:05:00Z",
                "type": "turn_context",
                "payload": {
                    "sandbox_policy": {"mode": "workspace-write"},
                    "approval_policy": "on-request",
                    "model": "gpt-5",
                    "effort": "medium",
                },
            },
            {
                "timestamp": "2026-04-10T10:06:00Z",
                "type": "message",
                "payload": {"role": "assistant", "text": "reply"},
            },
        ]
    )
    with rollout.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    return rollout


def write_bundle_manifest(
    bundle_dir: Path,
    *,
    session_id: str,
    relative_path: str = "",
    export_machine: str = "",
    export_machine_key: str = "",
    exported_at: str = "2026-04-11T10:00:00Z",
    updated_at: str = "2026-04-11T10:00:00Z",
    thread_name: str = "",
    session_cwd: str = "",
    session_kind: str = "desktop",
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bundle_dir / "manifest.env"
    relative_path = relative_path or f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    values = {
        "SESSION_ID": session_id,
        "RELATIVE_PATH": relative_path,
        "EXPORTED_AT": exported_at,
        "UPDATED_AT": updated_at,
        "THREAD_NAME": thread_name,
        "SESSION_CWD": session_cwd,
        "SESSION_SOURCE": "vscode",
        "SESSION_ORIGINATOR": "Codex Desktop",
        "SESSION_KIND": session_kind,
    }
    if export_machine:
        values["EXPORT_MACHINE"] = export_machine
    if export_machine_key:
        values["EXPORT_MACHINE_KEY"] = export_machine_key

    with manifest_path.open("w", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={shlex.quote(value)}\n")


class SupportHelperTests(unittest.TestCase):
    def test_long_path_is_noop_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX-only assertion")
        from ai_cli_kit.codex.support import _long_path

        self.assertEqual(_long_path(Path("/tmp/regular/path.txt")), "/tmp/regular/path.txt")

    def test_long_path_prefixes_long_windows_paths(self) -> None:
        # Strings (not pathlib.Path) are passed so the test works on POSIX: pathlib.Path
        # on Python 3.11 refuses to instantiate WindowsPath when os.name is patched to "nt",
        # but _long_path only needs os.fspath-able input and operates on the returned text.
        from unittest import mock

        from ai_cli_kit.codex import support

        with mock.patch.object(support.os, "name", "nt"):
            cases = [
                ("C:\\" + "a" * 260 + "\\file.txt", lambda s: "\\\\?\\" + s),
                ("\\\\server\\share\\" + "b" * 260 + "\\file.txt", lambda s: "\\\\?\\UNC\\" + s[2:]),
            ]
            for absolute, expected_fn in cases:
                with mock.patch.object(support.os.path, "abspath", return_value=absolute):
                    self.assertEqual(support._long_path(absolute), expected_fn(absolute))

            already_prefixed = "\\\\?\\C:\\already\\prefixed.txt"
            with mock.patch.object(support.os.path, "abspath") as abspath_mock:
                self.assertEqual(support._long_path(already_prefixed), already_prefixed)
                abspath_mock.assert_not_called()

            short = "C:\\short\\path.txt"
            with mock.patch.object(support.os.path, "abspath", return_value=short):
                self.assertEqual(support._long_path(short), short)

    def test_file_lock_serializes_cross_thread_writes(self) -> None:
        # Verify the advisory lock actually excludes contenders. Two threads race
        # to increment a counter stored in a small file; without the lock a
        # read-modify-write race would drop counts; with the lock final equals 200.
        import threading
        from ai_cli_kit.codex.support import file_lock

        with tempfile.TemporaryDirectory() as tmpdir:
            counter_path = Path(tmpdir) / "counter.txt"
            lock_path = Path(tmpdir) / "counter.lock"
            counter_path.write_text("0", encoding="utf-8")

            def bump(n: int) -> None:
                for _ in range(n):
                    with file_lock(lock_path):
                        current = int(counter_path.read_text(encoding="utf-8"))
                        counter_path.write_text(str(current + 1), encoding="utf-8")

            t1 = threading.Thread(target=bump, args=(100,))
            t2 = threading.Thread(target=bump, args=(100,))
            t1.start(); t2.start(); t1.join(); t2.join()
            self.assertEqual(counter_path.read_text(encoding="utf-8"), "200")

    def test_prune_old_backups_keeps_only_requested_count(self) -> None:
        import time
        from ai_cli_kit.codex.support import prune_old_backups

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "backups"
            root.mkdir()
            dirs = []
            for i in range(5):
                d = root / f"dir-{i}"
                d.mkdir()
                (d / "marker").write_text(str(i))
                # ensure distinct mtimes
                os.utime(d, (1_700_000_000 + i, 1_700_000_000 + i))
                dirs.append(d)

            removed = prune_old_backups(root, keep_last=2)
            self.assertEqual(len(removed), 3)
            self.assertEqual({d.name for d in removed}, {"dir-0", "dir-1", "dir-2"})
            remaining = sorted(p.name for p in root.iterdir())
            self.assertEqual(remaining, ["dir-3", "dir-4"])

    def test_prune_old_backups_noop_when_under_keep_last(self) -> None:
        from ai_cli_kit.codex.support import prune_old_backups

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "backups"
            root.mkdir()
            (root / "only").mkdir()
            removed = prune_old_backups(root, keep_last=5)
            self.assertEqual(removed, [])
            self.assertTrue((root / "only").exists())

    def test_atomic_write_preserves_lf_line_endings_on_all_platforms(self) -> None:
        # Ensures newline="" is wired: caller writes \n, file contains LF (0x0A)
        # on disk, not CRLF. Protects Codex CLI compatibility on Windows where
        # Python's text mode would otherwise translate \n → \r\n.
        from ai_cli_kit.codex.support import atomic_write

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "file.jsonl"
            with atomic_write(target) as fh:
                fh.write("line1\n")
                fh.write("line2\n")
            raw = target.read_bytes()
            self.assertEqual(raw, b"line1\nline2\n")
            self.assertNotIn(b"\r\n", raw)

    def test_safe_copy2_copies_and_preserves_mtime(self) -> None:
        from ai_cli_kit.codex.support import safe_copy2

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.txt"
            dst = Path(tmpdir) / "dst.txt"
            src.write_text("hello", encoding="utf-8")
            os.utime(src, (1_700_000_000, 1_700_000_000))
            safe_copy2(src, dst)
            self.assertEqual(dst.read_text(encoding="utf-8"), "hello")
            self.assertEqual(int(dst.stat().st_mtime), 1_700_000_000)


class SessionPreviewHelperTests(unittest.TestCase):
    def test_summarize_session_prompt_strips_ide_request_marker(self) -> None:
        from ai_cli_kit.codex.stores.session_files import summarize_session_prompt

        text = "# Context from my IDE setup:\n\n## Open tabs:\n- a.py\n\n## My request for Codex:\n修复 bug"
        self.assertEqual(summarize_session_prompt(text), "修复 bug")

    def test_summarize_session_prompt_strips_task_marker(self) -> None:
        from ai_cli_kit.codex.stores.session_files import summarize_session_prompt

        text = "# Resume context\n---\n## Task\n继续执行"
        self.assertEqual(summarize_session_prompt(text), "继续执行")

    def test_summarize_session_prompt_no_marker_returns_normalized_input(self) -> None:
        from ai_cli_kit.codex.stores.session_files import summarize_session_prompt

        self.assertEqual(summarize_session_prompt("  hello   world  "), "hello world")
        self.assertEqual(summarize_session_prompt(""), "")

    def test_is_placeholder_thread_name_detects_uuid_and_empty(self) -> None:
        from ai_cli_kit.codex.stores.session_files import is_placeholder_thread_name

        self.assertTrue(is_placeholder_thread_name(""))
        self.assertTrue(is_placeholder_thread_name("   "))
        self.assertTrue(is_placeholder_thread_name("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
        sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.assertTrue(is_placeholder_thread_name(sid, sid))
        self.assertFalse(is_placeholder_thread_name("Real thread title"))
        self.assertFalse(is_placeholder_thread_name("修复 bug"))

    def test_is_placeholder_thread_name_flags_session_meta_markers(self) -> None:
        from ai_cli_kit.codex.stores.session_files import is_placeholder_thread_name

        self.assertTrue(is_placeholder_thread_name("<environment_context>"))
        self.assertTrue(is_placeholder_thread_name("# AGENTS.md instructions"))


class IndexStoreHelperTests(unittest.TestCase):
    def test_remove_session_index_entries_drops_requested_ids(self) -> None:
        from ai_cli_kit.codex.stores.index import (
            load_existing_index,
            remove_session_index_entries,
            upsert_session_index,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = Path(tmpdir) / "session_index.jsonl"
            upsert_session_index(index_file, "sid-1", "first", "2026-04-01T00:00:00Z")
            upsert_session_index(index_file, "sid-2", "second", "2026-04-02T00:00:00Z")
            upsert_session_index(index_file, "sid-3", "third", "2026-04-03T00:00:00Z")

            remove_session_index_entries(index_file, {"sid-2"})
            remaining = load_existing_index(index_file)
            self.assertIn("sid-1", remaining)
            self.assertNotIn("sid-2", remaining)
            self.assertIn("sid-3", remaining)

    def test_remove_session_index_entries_is_noop_for_missing_file(self) -> None:
        from ai_cli_kit.codex.stores.index import remove_session_index_entries

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nope.jsonl"
            # Should not raise
            remove_session_index_entries(missing, {"sid-1"})
            self.assertFalse(missing.exists())


class CoreWorkflowTests(unittest.TestCase):
    def test_session_summaries_use_first_meaningful_user_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "10101010-1010-1010-1010-101010101010"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-a"),
                archived=True,
                user_message="https://github.com/xiaotian2333/newapi-checkin.git 把这个醒目拉下来看看",
                include_env_context=True,
            )

            summaries = get_session_summaries(CodexPaths(home=home))
            self.assertEqual(len(summaries), 1)
            self.assertEqual(
                summaries[0].preview,
                "https://github.com/xiaotian2333/newapi-checkin.git 把这个醒目拉下来看看",
            )

    def test_session_summaries_strip_ide_context_wrapper_from_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "30303030-3030-3030-3030-303030303030"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-b"),
                archived=True,
                user_message=(
                    "# Context from my IDE setup:\n\n"
                    "## Open tabs:\n"
                    "- config.toml: c:\\Users\\zhanghang\\.codex\\config.toml\n\n"
                    "## My request for Codex:\n"
                    "帮我看一下为什么标题回填成 UUID"
                ),
            )

            summaries = get_session_summaries(CodexPaths(home=home))
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].preview, "帮我看一下为什么标题回填成 UUID")

    def test_session_summaries_strip_resume_context_wrapper_from_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "31313131-3131-3131-3131-313131313131"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-c"),
                archived=True,
                user_message=(
                    "# Resume context (Codex History Viewer)\n"
                    "- Source: `example`\n"
                    "---\n"
                    "## Task\n"
                    "帮我继续排查为什么历史标题显示成 UUID"
                ),
            )

            summaries = get_session_summaries(CodexPaths(home=home))
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].preview, "帮我继续排查为什么历史标题显示成 UUID")

    def test_session_summaries_fall_back_to_workspace_name_for_windows_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "20202020-2020-2020-2020-202020202020"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=r"C:\Users\Alice\Projects\Cherry-Studio",
                archived=True,
            )

            summaries = get_session_summaries(CodexPaths(home=home))
            self.assertEqual(len(summaries), 1)
            self.assertIn("Cherry-Studio", summaries[0].preview)
            self.assertIn("2026-04-10 10:00", summaries[0].preview)

    def test_collect_known_bundle_summaries_infers_export_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            paths = CodexPaths(home=home, cwd=workspace)

            new_single = (
                workspace
                / "codex_sessions"
                / "MacBook-Pro-A"
                / "single"
                / "20260411-100000-000001"
                / "aaaa1111-1111-1111-1111-111111111111"
            )
            legacy_cli = (
                workspace
                / "codex_sessions"
                / "bundles"
                / "cli_batches"
                / "20260410-100000-000001"
                / "bbbb2222-2222-2222-2222-222222222222"
            )
            custom_dir = (
                workspace
                / "codex_sessions"
                / "bundles"
                / "manual_drop"
                / "cccc3333-3333-3333-3333-333333333333"
            )
            desktop_active = (
                workspace
                / "codex_sessions"
                / "Studio-Mac"
                / "active"
                / "20260411-110000-000001"
                / "dddd4444-4444-4444-4444-444444444444"
            )

            write_bundle_manifest(
                new_single,
                session_id="aaaa1111-1111-1111-1111-111111111111",
                export_machine="MacBook-Pro-A",
                export_machine_key="MacBook-Pro-A",
                thread_name="single export",
            )
            write_bundle_manifest(
                legacy_cli,
                session_id="bbbb2222-2222-2222-2222-222222222222",
                thread_name="legacy batch",
                session_kind="cli",
            )
            write_bundle_manifest(
                custom_dir,
                session_id="cccc3333-3333-3333-3333-333333333333",
                export_machine="Manual-Mac",
                export_machine_key="Manual-Mac",
                thread_name="custom layout",
            )
            write_bundle_manifest(
                desktop_active,
                session_id="dddd4444-4444-4444-4444-444444444444",
                export_machine="Studio-Mac",
                export_machine_key="Studio-Mac",
                thread_name="desktop active",
            )

            with pushd(workspace):
                summaries = collect_known_bundle_summaries(paths, limit=None)
                single_only = collect_known_bundle_summaries(paths, limit=None, export_group_filter="single")

            by_id = {summary.session_id: summary for summary in summaries}
            self.assertEqual(by_id["aaaa1111-1111-1111-1111-111111111111"].export_group, "single")
            self.assertEqual(by_id["aaaa1111-1111-1111-1111-111111111111"].export_group_label, "single")
            self.assertEqual(by_id["bbbb2222-2222-2222-2222-222222222222"].export_group, "cli")
            self.assertEqual(by_id["bbbb2222-2222-2222-2222-222222222222"].export_group_label, "cli")
            self.assertEqual(by_id["cccc3333-3333-3333-3333-333333333333"].export_group, "custom")
            self.assertEqual(by_id["cccc3333-3333-3333-3333-333333333333"].export_group_label, "自定义目录")
            self.assertEqual(by_id["dddd4444-4444-4444-4444-444444444444"].export_group, "active")
            self.assertEqual([item.session_id for item in single_only], ["aaaa1111-1111-1111-1111-111111111111"])

    def test_latest_distinct_bundle_summaries_keeps_newest_per_machine_and_session(self) -> None:
        rows = [
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/new"),
                relative_path="sessions/x",
                updated_at="2026-04-11T10:00:00Z",
                exported_at="2026-04-11T10:00:00Z",
                thread_name="new",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
            ),
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/old"),
                relative_path="sessions/x",
                updated_at="2026-04-10T10:00:00Z",
                exported_at="2026-04-10T10:00:00Z",
                thread_name="old",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
            ),
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/other-machine"),
                relative_path="sessions/x",
                updated_at="2026-04-09T10:00:00Z",
                exported_at="2026-04-09T10:00:00Z",
                thread_name="other-machine",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-2",
                source_machine_key="machine-2",
            ),
        ]

        latest = latest_distinct_bundle_summaries(rows)
        self.assertEqual([item.bundle_dir for item in latest], [Path("/tmp/new"), Path("/tmp/other-machine")])

    def test_latest_distinct_bundle_summaries_ignores_root_group_for_same_machine(self) -> None:
        rows = [
            BundleSummary(
                source_group="bundle",
                session_id="session-a",
                bundle_dir=Path("/tmp/single"),
                relative_path="sessions/x",
                updated_at="2026-04-11T09:00:00Z",
                exported_at="2026-04-11T09:00:00Z",
                thread_name="single export",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
                export_group="single",
                export_group_label="single",
            ),
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/desktop-active"),
                relative_path="sessions/x",
                updated_at="2026-04-11T10:00:00Z",
                exported_at="2026-04-11T10:00:00Z",
                thread_name="desktop active",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
                export_group="active",
                export_group_label="active",
            ),
        ]

        latest = latest_distinct_bundle_summaries(rows)
        self.assertEqual([item.bundle_dir for item in latest], [Path("/tmp/desktop-active")])

    def test_clone_to_provider_creates_lineage_preserving_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            original_cwd = workspace / "project-a"
            original_cwd.mkdir()
            original_id = "11111111-1111-1111-1111-111111111111"
            write_session(
                home,
                original_id,
                provider="old-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=original_cwd,
            )
            write_history(home, original_id, "hello clone")
            paths = CodexPaths(home=home, cwd=workspace)

            with pushd(workspace):
                result = clone_to_provider(paths)

            self.assertEqual(result.stats["cloned"], 1)
            sessions = list(iter_session_files(paths, active_only=True))
            self.assertEqual(len(sessions), 2)
            cloned_file = next(path for path in sessions if original_id not in path.name)
            cloned_payload = read_session_payload(cloned_file)
            self.assertEqual(cloned_payload["model_provider"], "target-provider")
            self.assertEqual(cloned_payload["cloned_from"], original_id)
            self.assertEqual(cloned_payload["original_provider"], "old-provider")

    def test_dedupe_clones_removes_duplicate_clone_and_cleans_index_and_threads(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            write_state_file(home)
            create_threads_db(home)

            project_cwd = workspace / "project-d"
            project_cwd.mkdir()
            original_id = "41414141-4141-4141-4141-414141414141"
            write_session(
                home,
                original_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_cwd,
                archived=False,
                user_message="修复重复 clone",
            )

            paths = CodexPaths(home=home, cwd=workspace)
            clone_to_provider(paths, target_provider="target-provider", dry_run=False)
            repair_desktop(paths, target_provider="target-provider")

            sessions = list(iter_session_files(paths, active_only=False))
            clone_sessions = [path for path in sessions if read_session_payload(path).get("cloned_from") == original_id]
            self.assertEqual(len(clone_sessions), 1)
            clone_path = clone_sessions[0]
            clone_id = read_session_payload(clone_path)["id"]

            dry_run_result = dedupe_clones(paths, target_provider="target-provider", dry_run=True)
            self.assertEqual(len(dry_run_result.duplicate_pairs), 1)

            result = dedupe_clones(paths, target_provider="target-provider", dry_run=False)
            self.assertEqual(len(result.deleted_session_ids), 1)
            self.assertEqual(result.deleted_session_ids[0], clone_id)
            self.assertFalse(clone_path.exists())

            index_entries = load_existing_index(home / ".codex" / "session_index.jsonl")
            self.assertIn(original_id, index_entries)
            self.assertNotIn(clone_id, index_entries)

            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            count = conn.execute("select count(*) from threads where id = ?", (clone_id,)).fetchone()[0]
            conn.close()
            self.assertEqual(count, 0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dedupe_clones_skips_chain_intermediates(self) -> None:
        # Build A→B→C chain: B is A's clone, C is B's clone. Running dedupe should
        # keep B (chain intermediate) to avoid orphaning C's lineage; only leaf pair
        # without downstream clones qualifies.
        tmpdir = tempfile.mkdtemp()
        try:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            project_cwd = workspace / "project-chain"
            project_cwd.mkdir()

            a_id = "aaaaaaaa-0000-4000-8000-000000000001"
            b_id = "bbbbbbbb-0000-4000-8000-000000000002"
            c_id = "cccccccc-0000-4000-8000-000000000003"

            write_session(home, a_id, provider="old-provider", source="vscode",
                          originator="Codex Desktop", cwd=project_cwd)
            # B is A's clone, in target provider
            write_session(home, b_id, provider="target-provider", source="vscode",
                          originator="Codex Desktop", cwd=project_cwd,
                          cloned_from=a_id)
            # C is B's clone, also in target provider
            write_session(home, c_id, provider="target-provider", source="vscode",
                          originator="Codex Desktop", cwd=project_cwd,
                          cloned_from=b_id)

            paths = CodexPaths(home=home, cwd=workspace)

            result = dedupe_clones(paths, target_provider="target-provider", dry_run=True)

            # Only (delete C, keep B) is safe: deleting B would orphan C.
            delete_paths = {str(p) for p, _, _ in result.duplicate_pairs}
            self.assertEqual(len(result.duplicate_pairs), 1)
            self.assertTrue(any(c_id in p for p in delete_paths))
            self.assertFalse(any(b_id in p for p in delete_paths))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_clone_to_provider_is_idempotent_after_first_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            original_cwd = workspace / "project-b"
            original_cwd.mkdir()
            original_id = "12111111-1111-1111-1111-111111111111"
            write_session(
                home,
                original_id,
                provider="old-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=original_cwd,
            )
            paths = CodexPaths(home=home, cwd=workspace)

            with pushd(workspace):
                first_result = clone_to_provider(paths)
                second_result = clone_to_provider(paths)

            self.assertEqual(first_result.stats["cloned"], 1)
            self.assertEqual(second_result.stats["cloned"], 0)
            self.assertEqual(second_result.stats["skipped_exists"], 1)

            sessions = list(iter_session_files(paths, active_only=True))
            self.assertEqual(len(sessions), 2)

            cloned_files = [
                path for path in sessions if read_session_payload(path).get("cloned_from") == original_id
            ]
            self.assertEqual(len(cloned_files), 1)

    def test_export_validate_and_import_roundtrip_updates_desktop_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "22222222-2222-2222-2222-222222222222"
            missing_cwd = workspace / "missing-project"
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=missing_cwd,
            )
            write_history(src_home, session_id, "roundtrip bundle")

            src_paths = CodexPaths(home=src_home, cwd=workspace)
            dst_paths = CodexPaths(home=dst_home, cwd=workspace)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MacBook-Pro-A"):
                export_result = export_session(src_paths, session_id)
                validation = validate_bundles(src_paths, source_group="bundle")
                summaries = get_bundle_summaries(src_paths, source_group="bundle")
                machine_filtered = get_bundle_summaries(
                    src_paths,
                    source_group="bundle",
                    machine_filter=machine_label_to_key("MacBook-Pro-A"),
                )
                import_result = import_session(dst_paths, str(export_result.bundle_dir), desktop_visible=True)

            self.assertEqual(len(validation.valid_results), 1)
            self.assertEqual(validation.invalid_results, [])
            self.assertEqual(len(summaries), 1)
            self.assertEqual(len(machine_filtered), 1)
            self.assertEqual(summaries[0].source_machine, "MacBook-Pro-A")
            self.assertEqual(summaries[0].source_machine_key, machine_label_to_key("MacBook-Pro-A"))
            self.assertTrue(import_result.created_workspace_dir)
            self.assertTrue(import_result.desktop_registered)
            self.assertTrue(import_result.thread_row_upserted)
            self.assertTrue(missing_cwd.is_dir())

            target_session = dst_home / ".codex" / export_result.relative_path
            self.assertTrue(target_session.is_file())
            self.assertIn(machine_label_to_key("MacBook-Pro-A"), export_result.bundle_dir.parts)

            state_data = json.loads((dst_home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertIn(str(missing_cwd), state_data["electron-saved-workspace-roots"])

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select source, model_provider, cwd from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("vscode", "target-provider", str(missing_cwd)))

    def test_repair_desktop_rebuilds_index_and_converts_cli_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            create_threads_db(home)

            desktop_cwd = workspace / "desktop-project"
            cli_cwd = workspace / "cli-project"
            desktop_cwd.mkdir()
            cli_cwd.mkdir()

            desktop_id = "33333333-3333-3333-3333-333333333333"
            cli_id = "44444444-4444-4444-4444-444444444444"
            write_session(
                home,
                desktop_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=desktop_cwd,
            )
            write_session(
                home,
                cli_id,
                provider="old-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=cli_cwd,
            )
            write_history(home, desktop_id, "desktop message")
            write_history(home, cli_id, "cli message")

            paths = CodexPaths(home=home)
            result = repair_desktop(paths, include_cli=True)

            self.assertEqual(result.desktop_retagged, 1)
            self.assertEqual(result.cli_converted, 1)
            self.assertEqual(result.threads_updated, 2)

            desktop_payload = read_session_payload(
                home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{desktop_id}.jsonl"
            )
            cli_payload = read_session_payload(
                home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{cli_id}.jsonl"
            )
            self.assertEqual(desktop_payload["model_provider"], "repaired-provider")
            self.assertEqual(cli_payload["model_provider"], "repaired-provider")
            self.assertEqual(cli_payload["source"], "vscode")
            self.assertEqual(cli_payload["originator"], "Codex Desktop")

            index_lines = (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index_lines), 2)

            state_data = json.loads((home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertIn(str(desktop_cwd), state_data["electron-saved-workspace-roots"])
            self.assertIn(str(cli_cwd), state_data["electron-saved-workspace-roots"])

    def test_repair_desktop_uses_session_preview_when_thread_name_is_uuid_placeholder(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            write_state_file(home)
            create_threads_db(home)

            project_cwd = workspace / "project"
            project_cwd.mkdir()

            clone_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            write_session(
                home,
                clone_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_cwd,
                user_message="修复波动监控标题回填逻辑",
            )

            paths = CodexPaths(home=home)
            result = repair_desktop(paths)

            self.assertEqual(result.threads_updated, 1)

            index_entry = load_existing_index(home / ".codex" / "session_index.jsonl")[clone_id]
            self.assertEqual(index_entry["thread_name"], "修复波动监控标题回填逻辑")

            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            row = conn.execute(
                "select title, first_user_message from threads where id = ?",
                (clone_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("修复波动监控标题回填逻辑", "修复波动监控标题回填逻辑"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_import_preserves_newer_local_session_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "55555555-5555-5555-5555-555555555555"
            src_cwd = workspace / "src-project"
            dst_cwd = workspace / "dst-project"
            src_cwd.mkdir()
            dst_cwd.mkdir()

            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=src_cwd,
                timestamp="2026-04-10T10:00:00Z",
            )
            write_history(src_home, session_id, "older imported history")

            write_session(
                dst_home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=dst_cwd,
                timestamp="2026-04-11T12:00:00Z",
            )
            write_history(dst_home, session_id, "newer local history")

            src_paths = CodexPaths(home=src_home, cwd=workspace)
            dst_paths = CodexPaths(home=dst_home, cwd=workspace)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Work-Laptop"):
                export_result = export_session(src_paths, session_id)
                import_result = import_session(dst_paths, str(export_result.bundle_dir), desktop_visible=True)

            self.assertEqual(import_result.rollout_action, "preserved_newer_local")

            target_session = dst_home / ".codex" / export_result.relative_path
            target_payload = read_session_payload(target_session)
            self.assertEqual(target_payload["model_provider"], "target-provider")
            self.assertEqual(target_payload["cwd"], str(dst_cwd))
            self.assertEqual(target_payload["timestamp"], "2026-04-11T12:00:00Z")

            history_lines = (dst_home / ".codex" / "history.jsonl").read_text(encoding="utf-8")
            self.assertIn("older imported history", history_lines)
            self.assertIn("newer local history", history_lines)

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select model_provider, cwd from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("target-provider", str(dst_cwd)))

    def test_import_session_resolves_desktop_bundle_by_session_id_with_machine_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "66666666-6666-6666-6666-666666666666"
            project_dir = workspace / "desktop-project"
            project_dir.mkdir()
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_dir,
            )
            write_history(src_home, session_id, "desktop bundle by session id")

            src_paths = CodexPaths(home=src_home, cwd=workspace)
            dst_paths = CodexPaths(home=dst_home, cwd=workspace)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Studio-Mac"):
                export_active_desktop_all(src_paths)
                result = import_session(
                    dst_paths,
                    session_id,
                    source_group="desktop",
                    machine_filter=machine_label_to_key("Studio-Mac"),
                    desktop_visible=True,
                )

            self.assertTrue(result.resolved_from_session_id)
            self.assertIn("active", result.bundle_dir.parts)
            self.assertIn(machine_label_to_key("Studio-Mac"), result.bundle_dir.parts)

    def test_export_session_normalizes_manifest_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "source-provider")

            session_id = "99999999-9999-9999-9999-999999999999"
            project_dir = workspace / "project"
            project_dir.mkdir()
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_dir,
            )
            write_history(home, session_id, "normalize manifest path")

            paths = CodexPaths(home=home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Win-Machine"):
                result = export_session(paths, session_id)

            manifest = load_manifest(result.bundle_dir / "manifest.env")
            self.assertEqual(
                manifest["RELATIVE_PATH"],
                f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl",
            )

    def test_resolve_bundle_by_session_id_is_case_insensitive(self) -> None:
        # Bundle exported with mixed-case id 'ABc...' should resolve when looked
        # up with lowercase 'abc...' (case-insensitive FS on Windows/macOS).
        from ai_cli_kit.codex.stores.bundles import resolve_known_bundle_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            mixed_id = "ABcDEFab-abab-4aba-8aba-ABCDEFabcdef"
            bundle_dir = (
                workspace
                / "codex_sessions"
                / "MixedHost"
                / "single"
                / "20260414-120000-000001"
                / mixed_id
            )
            bundle_dir.mkdir(parents=True, exist_ok=True)
            write_bundle_manifest(
                bundle_dir,
                session_id=mixed_id,
                export_machine="MixedHost",
                export_machine_key="MixedHost",
                session_cwd=str(workspace / "proj"),
            )
            (bundle_dir / "history.jsonl").write_text("", encoding="utf-8")
            rel_session = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10" \
                / f"rollout-2026-04-10T10-00-00-{mixed_id}.jsonl"
            rel_session.parent.mkdir(parents=True, exist_ok=True)
            rel_session.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": mixed_id}}) + "\n",
                encoding="utf-8",
            )

            with pushd(workspace):
                paths = CodexPaths(home=Path(tmpdir) / "home", cwd=workspace)
                lower = mixed_id.lower()
                resolved = resolve_known_bundle_dir(paths, lower)
            self.assertEqual(resolved, bundle_dir)

    def test_import_and_validate_accept_windows_manifest_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "12121212-3434-5656-7878-909090909090"
            bundle_dir = (
                workspace
                / "codex_sessions"
                / "Windows-PC"
                / "single"
                / "20260411-100000-000001"
                / session_id
            )
            session_rel = Path("sessions/2026/03/19") / f"rollout-2026-03-19T22-00-41-{session_id}.jsonl"
            bundled_session = bundle_dir / "codex" / session_rel
            bundled_session.parent.mkdir(parents=True, exist_ok=True)
            bundled_session.write_text(
                "\n".join([
                    '{"timestamp":"2026-03-19T22:00:41Z","type":"session_meta","payload":{"id":"' + session_id + '","model_provider":"source-provider","source":"vscode","originator":"Codex Desktop","cwd":"' + str(workspace / "project") + '","timestamp":"2026-03-19T22:00:41Z","cli_version":"0.1.0"}}',
                    '{"timestamp":"2026-03-19T22:05:00Z","type":"message","payload":{"role":"assistant","text":"reply"}}',
                ]) + "\n",
                encoding="utf-8",
            )
            (bundle_dir / "history.jsonl").write_text(
                '{"session_id":"' + session_id + '","text":"windows bundle"}\n',
                encoding="utf-8",
            )
            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                relative_path=f"sessions\\2026\\03\\19\\rollout-2026-03-19T22-00-41-{session_id}.jsonl",
                export_machine="Windows-PC",
                export_machine_key="Windows-PC",
                session_cwd=str(workspace / "project"),
            )

            with pushd(workspace):
                paths = CodexPaths(home=dst_home, cwd=workspace)
                validation = validate_bundles(paths)
                self.assertEqual(len(validation.results), 1)
                self.assertTrue(validation.results[0].is_valid, validation.results[0].message)
                result = import_session(paths, str(bundle_dir), desktop_visible=True)

            self.assertEqual(
                result.relative_path,
                f"sessions/2026/03/19/rollout-2026-03-19T22-00-41-{session_id}.jsonl",
            )
            self.assertTrue(
                (
                    dst_home
                    / ".codex"
                    / "sessions"
                    / "2026"
                    / "03"
                    / "19"
                    / f"rollout-2026-03-19T22-00-41-{session_id}.jsonl"
                ).exists()
            )

    def test_import_session_uses_session_preview_when_bundle_thread_name_is_uuid(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "abababab-abab-4aba-8aba-abababababab"
            bundle_dir = (
                workspace
                / "codex_sessions"
                / "Windows-PC"
                / "single"
                / "20260411-100000-000001"
                / session_id
            )
            session_rel = Path("sessions/2026/04/10") / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            bundled_session = bundle_dir / "codex" / session_rel
            bundled_session.parent.mkdir(parents=True, exist_ok=True)
            with bundled_session.open("w", encoding="utf-8") as fh:
                for item in [
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "source-provider",
                            "source": "vscode",
                            "originator": "Codex Desktop",
                            "cwd": str(workspace / "project"),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    {
                        "timestamp": "2026-04-10T10:01:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "修复导入标题回填"}],
                        },
                    },
                ]:
                    fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            (bundle_dir / "history.jsonl").write_text("", encoding="utf-8")
            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                relative_path=f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl",
                export_machine="Windows-PC",
                export_machine_key="Windows-PC",
                thread_name=session_id,
                session_cwd=str(workspace / "project"),
            )

            with pushd(workspace):
                paths = CodexPaths(home=dst_home, cwd=workspace)
                result = import_session(paths, str(bundle_dir), desktop_visible=True)

            self.assertEqual(result.session_id, session_id)

            index_entry = load_existing_index(dst_home / ".codex" / "session_index.jsonl")[session_id]
            self.assertEqual(index_entry["thread_name"], "修复导入标题回填")

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute(
                "select title, first_user_message from threads where id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("修复导入标题回填", "修复导入标题回填"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_import_desktop_all_filters_machine_and_latest_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            other_home = Path(tmpdir) / "other_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(other_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            target_session_id = "77777777-7777-7777-7777-777777777777"
            other_session_id = "88888888-8888-8888-8888-888888888888"
            target_project = workspace / "target-project"
            other_project = workspace / "other-project"
            target_project.mkdir()
            other_project.mkdir()

            write_session(
                src_home,
                target_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=target_project,
                timestamp="2026-04-10T10:00:00Z",
            )
            write_history(src_home, target_session_id, "older desktop export")

            write_session(
                other_home,
                other_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=other_project,
            )
            write_history(other_home, other_session_id, "other machine export")

            src_paths = CodexPaths(home=src_home, cwd=workspace)
            other_paths = CodexPaths(home=other_home, cwd=workspace)
            dst_paths = CodexPaths(home=dst_home, cwd=workspace)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Work-Laptop"):
                export_active_desktop_all(src_paths)
                write_session(
                    src_home,
                    target_session_id,
                    provider="source-provider",
                    source="vscode",
                    originator="Codex Desktop",
                    cwd=target_project,
                    timestamp="2026-04-11T12:00:00Z",
                )
                export_active_desktop_all(src_paths)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Office-iMac"):
                export_active_desktop_all(other_paths)

            with pushd(workspace):
                result = import_desktop_all(
                    dst_paths,
                    machine_filter=machine_label_to_key("Work-Laptop"),
                    latest_only=True,
                    desktop_visible=True,
                )

            self.assertEqual(len(result.bundle_dirs), 1)
            self.assertEqual(len(result.success_dirs), 1)
            self.assertEqual(result.machine_filter, machine_label_to_key("Work-Laptop"))
            self.assertEqual(result.machine_label, "Work-Laptop")
            self.assertTrue(result.latest_only)

            imported_payload = read_session_payload(dst_home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{target_session_id}.jsonl")
            self.assertEqual(imported_payload["timestamp"], "2026-04-11T12:00:00Z")

            self.assertFalse(
                (dst_home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{other_session_id}.jsonl").exists()
            )


class RglobOSErrorHardeningTests(unittest.TestCase):
    """Lock OSError protection on cross-platform rglob hot paths.

    Why: pathlib.Path.rglob can raise OSError mid-walk if a subdirectory is
    deleted by another process (Codex itself, AV scanner, sync client). The
    TUI session list and bundle browser must degrade gracefully — empty
    result, not crash.
    """

    def test_iter_session_files_swallows_rglob_oserror(self) -> None:
        from unittest.mock import patch

        tmpdir = tempfile.mkdtemp()
        try:
            home = Path(tmpdir) / "home"
            (home / ".codex" / "sessions").mkdir(parents=True)
            (home / ".codex" / "archived_sessions").mkdir(parents=True)
            paths = CodexPaths(home=home, cwd=Path(tmpdir))

            with patch("pathlib.Path.rglob", side_effect=OSError("ENOENT (simulated)")):
                result = list(iter_session_files(paths))
            self.assertEqual(result, [])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_iter_bundle_directories_swallows_rglob_oserror(self) -> None:
        from unittest.mock import patch

        from ai_cli_kit.codex.stores.bundles import iter_bundle_directories_under_root

        tmpdir = tempfile.mkdtemp()
        try:
            bundle_root = Path(tmpdir) / "bundles"
            bundle_root.mkdir()

            with patch("pathlib.Path.rglob", side_effect=OSError("ENOENT (simulated)")):
                result = iter_bundle_directories_under_root(bundle_root)
            self.assertEqual(result, [])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
