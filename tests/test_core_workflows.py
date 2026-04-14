import json
import os
import shlex
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

from codex_session_toolkit.paths import CodexPaths  # noqa: E402
from codex_session_toolkit.models import BundleSummary  # noqa: E402
from codex_session_toolkit.services.browse import get_bundle_summaries, get_session_summaries, validate_bundles  # noqa: E402
from codex_session_toolkit.services.clone import clone_to_provider  # noqa: E402
from codex_session_toolkit.services.exporting import export_active_desktop_all, export_session  # noqa: E402
from codex_session_toolkit.services.importing import import_desktop_all, import_session  # noqa: E402
from codex_session_toolkit.services.repair import repair_desktop  # noqa: E402
from codex_session_toolkit.support import machine_label_to_key  # noqa: E402
from codex_session_toolkit.stores.bundles import collect_known_bundle_summaries, latest_distinct_bundle_summaries  # noqa: E402
from codex_session_toolkit.stores.session_files import iter_session_files, read_session_payload  # noqa: E402
from codex_session_toolkit.validation import load_manifest, validate_relative_path  # noqa: E402


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
) -> Path:
    base = home / ".codex" / ("archived_sessions" if archived else "sessions") / "2026" / "04" / "10"
    base.mkdir(parents=True, exist_ok=True)
    rollout = base / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    lines = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "model_provider": provider,
                "source": source,
                "originator": originator,
                "cwd": str(cwd),
                "timestamp": timestamp,
                "cli_version": "0.1.0",
            },
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


if __name__ == "__main__":
    unittest.main()
