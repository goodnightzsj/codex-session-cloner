"""Canonical CLI command parser and dispatcher."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import APP_COMMAND
from .errors import ToolkitError
from .paths import CodexPaths
from .presenters.reports import (
    print_archived_cleanup_result,
    print_batch_export_result,
    print_batch_import_result,
    print_bundle_rows,
    print_cleanup_result,
    print_clone_run_result,
    print_dedupe_result,
    print_export_result,
    print_import_result,
    print_provider_watch_event,
    print_repair_result,
    print_session_rows,
    print_validation_report,
)
from .services.browse import get_bundle_summaries, get_session_summaries, validate_bundles
from .services.archive_cleanup import clean_archived_sessions
from .services.clone import cleanup_clones, clone_to_provider
from .services.dedupe import dedupe_clones
from .services.exporting import export_active_desktop_all, export_cli_all, export_desktop_all, export_session
from .services.importing import import_desktop_all, import_session
from .services.repair import repair_desktop
from .services.watch import DEFAULT_WATCH_INTERVAL_SECONDS, iter_provider_watch_events
from .support import build_single_export_root


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description="Codex session clone/export/import/repair toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List local sessions")
    list_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")

    list_bundles_parser = subparsers.add_parser("list-bundles", help="List available bundle exports")
    list_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_bundles_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")
    list_bundles_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Which bundle categories to scan",
    )

    validate_bundles_parser = subparsers.add_parser("validate-bundles", help="Validate exported bundle directories")
    validate_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    validate_bundles_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Which bundle categories to scan",
    )
    validate_bundles_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for validation count (0 means no limit)",
    )
    validate_bundles_parser.add_argument("--verbose", action="store_true", help="Print successful bundle validations too")

    clone_parser = subparsers.add_parser("clone-provider", help="Clone active sessions to the target provider")
    clone_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clone_parser.add_argument("--dry-run", action="store_true")

    watch_parser = subparsers.add_parser(
        "watch-provider",
        help="Watch provider changes and clone active sessions automatically",
    )
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_WATCH_INTERVAL_SECONDS,
        help=f"Polling interval in seconds (default: {DEFAULT_WATCH_INTERVAL_SECONDS:g})",
    )
    watch_parser.add_argument("--dry-run", action="store_true")
    watch_parser.add_argument(
        "--no-initial-sync",
        action="store_true",
        help="Only sync after a provider change observed after startup",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Run one provider watch check and exit",
    )

    clean_parser = subparsers.add_parser("clean-clones", help="Delete legacy unmarked clone files")
    clean_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clean_parser.add_argument("--dry-run", action="store_true")

    archived_clean_parser = subparsers.add_parser("clean-archived", help="Delete archived Codex threads safely")
    archived_clean_parser.add_argument("--dry-run", action="store_true")
    archived_clean_parser.add_argument("--yes", action="store_true", help="Confirm destructive cleanup")

    dedupe_parser = subparsers.add_parser(
        "dedupe-clones",
        help="Remove duplicate cloned sessions when the original still exists",
    )
    dedupe_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    dedupe_parser.add_argument("--dry-run", action="store_true")

    export_parser = subparsers.add_parser("export", help="Export one session bundle")
    export_parser.add_argument("session_id")

    export_all_parser = subparsers.add_parser("export-desktop-all", help="Export all Desktop sessions in bulk")
    export_all_parser.add_argument("--dry-run", action="store_true")
    export_all_parser.add_argument("--active-only", action="store_true", help="Legacy compatibility flag")

    export_active_desktop_parser = subparsers.add_parser(
        "export-active-desktop-all",
        help="Export all active Desktop sessions in bulk",
    )
    export_active_desktop_parser.add_argument("--dry-run", action="store_true")

    export_cli_parser = subparsers.add_parser("export-cli-all", help="Export all CLI sessions in bulk")
    export_cli_parser.add_argument("--dry-run", action="store_true")

    import_parser = subparsers.add_parser("import", help="Import one session bundle")
    import_parser.add_argument("input_value", help="Session id or bundle directory")
    import_parser.add_argument("--desktop-visible", action="store_true")
    import_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Which bundle categories to scan when importing by session id",
    )
    import_parser.add_argument("--machine", default="", help="Only search bundles from this machine key")
    import_parser.add_argument("--export-group", default="", help="Only search bundles from this export folder (desktop/active/cli/single)")

    import_all_parser = subparsers.add_parser("import-desktop-all", help="Import one machine/category bundle folder in bulk")
    import_all_parser.add_argument("--desktop-visible", action="store_true")
    import_all_parser.add_argument("--machine", default="", help="Only import bundles from this machine key")
    import_all_parser.add_argument("--export-group", default="", help="Only import bundles from this export folder (desktop/active/cli/single)")
    import_all_parser.add_argument("--latest-only", action="store_true", help="Only import the latest bundle per machine and session id")

    repair_parser = subparsers.add_parser("repair-desktop", help="Repair Desktop sidebar visibility")
    repair_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.add_argument("--include-cli", action="store_true")

    return parser


def run_cli(argv: Sequence[str], *, paths: Optional[CodexPaths] = None) -> int:
    paths = paths or CodexPaths()
    parser = create_parser()
    args = parser.parse_args(list(argv))

    if args.command == "list":
        return print_session_rows(get_session_summaries(paths, pattern=args.pattern, limit=max(1, args.limit)))
    if args.command == "list-bundles":
        return print_bundle_rows(
            get_bundle_summaries(
                paths,
                pattern=args.pattern,
                limit=max(1, args.limit),
                source_group=args.source,
            )
        )
    if args.command == "validate-bundles":
        return print_validation_report(
            validate_bundles(
                paths,
                pattern=args.pattern,
                source_group=args.source,
                limit=(None if args.limit <= 0 else args.limit),
            ),
            verbose=args.verbose,
        )
    if args.command == "clone-provider":
        return print_clone_run_result(clone_to_provider(paths, target_provider=args.target_provider, dry_run=args.dry_run))
    if args.command == "watch-provider":
        if not args.once:
            print(
                f"Watching provider changes in {paths.config_file} every {args.interval:g}s. "
                "Press Ctrl+C to stop.",
                flush=True,
            )
        exit_code = 0
        try:
            for event in iter_provider_watch_events(
                paths,
                interval_seconds=args.interval,
                dry_run=args.dry_run,
                sync_on_start=not args.no_initial_sync,
                max_checks=1 if args.once else None,
            ):
                exit_code = max(exit_code, print_provider_watch_event(event))
        except KeyboardInterrupt:
            print("\nStopped provider watch.")
            return 130
        return exit_code
    if args.command == "clean-clones":
        return print_cleanup_result(cleanup_clones(paths, target_provider=args.target_provider, dry_run=args.dry_run))
    if args.command == "clean-archived":
        if not args.dry_run and not args.yes:
            raise ToolkitError("clean-archived deletes archived threads. Re-run with --dry-run first, or pass --yes to confirm.")
        return print_archived_cleanup_result(clean_archived_sessions(paths, dry_run=args.dry_run))
    if args.command == "dedupe-clones":
        return print_dedupe_result(dedupe_clones(paths, target_provider=args.target_provider, dry_run=args.dry_run))
    if args.command == "export":
        return print_export_result(
            export_session(
                paths,
                args.session_id,
                bundle_root=build_single_export_root(paths.default_bundle_root),
            )
        )
    if args.command == "export-desktop-all":
        return print_batch_export_result(export_desktop_all(paths, dry_run=args.dry_run, active_only=args.active_only))
    if args.command == "export-active-desktop-all":
        return print_batch_export_result(export_active_desktop_all(paths, dry_run=args.dry_run))
    if args.command == "export-cli-all":
        return print_batch_export_result(export_cli_all(paths, dry_run=args.dry_run))
    if args.command == "import":
        return print_import_result(
            import_session(
                paths,
                args.input_value,
                source_group=args.source,
                machine_filter=args.machine,
                export_group_filter=args.export_group,
                desktop_visible=args.desktop_visible,
            )
        )
    if args.command == "import-desktop-all":
        return print_batch_import_result(
            import_desktop_all(
                paths,
                machine_filter=args.machine,
                export_group_filter=args.export_group,
                latest_only=args.latest_only,
                desktop_visible=args.desktop_visible,
            )
        )
    if args.command == "repair-desktop":
        return print_repair_result(
            repair_desktop(
                paths,
                target_provider=args.target_provider,
                dry_run=args.dry_run,
                include_cli=args.include_cli,
            )
        )

    raise ToolkitError(f"Unknown command: {args.command}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        return run_cli(argv)
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
