"""Command-line entry point for inspection and concurrent Airtable runs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from collections.abc import Sequence

from playwright.sync_api import Error as PlaywrightError

from backend.airtable_actions import list_actions
from backend.browser import BrowserLaunchError, BrowserSession, NavigationError
from backend.config import AppConfig, Settings, configure_logging, load_apps, _validate_airtable_url
from backend.inspector import InspectionRecorder, install_inspector, wait_for_inspections, capture_page_structure
from backend.reporting import generate_excel_report
from backend.runner import ConcurrentRunner


LOGGER = logging.getLogger(__name__)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Airtable interfaces and run safe concurrent actions."
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect_command = subcommands.add_parser(
        "inspect", help="Open one Airtable app and capture ALT-clicked elements"
    )
    inspect_target = inspect_command.add_mutually_exclusive_group()
    inspect_target.add_argument("--app", help="App name from apps.json")
    inspect_target.add_argument("--url", help="Explicit Airtable URL")
    inspect_command.add_argument("--snapshot", action="store_true",
                                 help="Capture page/frame structure automatically and exit")
    inspect_command.add_argument("--label", default="page",
                                 help="Task or step name included in inspection filenames")

    run_command = subcommands.add_parser(
        "run", help="Run actions concurrently across configured apps"
    )
    run_command.add_argument("actions", nargs="+", choices=list_actions())
    run_target = run_command.add_mutually_exclusive_group()
    run_target.add_argument("--url", help="Run on one explicit Airtable URL")
    run_target.add_argument(
        "--app",
        action="append",
        dest="apps",
        help="Run only this named app; may be provided more than once",
    )
    run_command.add_argument(
        "--concurrency",
        type=_positive_int,
        help="Override the CONCURRENCY setting for this run",
    )

    subcommands.add_parser("list-actions", help="List registered action names")
    subcommands.add_parser("list-apps", help="List configured app names")
    return parser


def _select_apps(apps: tuple[AppConfig, ...], names: list[str] | None) -> tuple[AppConfig, ...]:
    if not names:
        return apps
    requested = set(names)
    selected = tuple(app for app in apps if app.name in requested)
    missing = requested - {app.name for app in selected}
    if missing:
        available = ", ".join(app.name for app in apps)
        raise ValueError(
            f"Unknown app name(s): {', '.join(sorted(missing))}. Available: {available}"
        )
    return selected


def _open_and_inspect(
    settings: Settings,
    apps: tuple[AppConfig, ...],
    app_name: str | None,
    explicit_url: str | None,
    snapshot: bool = False,
    label: str = "page",
) -> int:
    selected = _select_apps(apps, [app_name] if app_name else None)
    target_url = explicit_url or selected[0].url
    matching_app = next((app for app in apps if app.url == target_url), None)
    base_id = re.search(r"/(app[A-Za-z0-9]+)", target_url)
    inspection_name = (matching_app.name if matching_app else
                       base_id.group(1) if base_id else "airtable")
    with BrowserSession(settings) as browser:
        assert browser.context is not None
        recorder = InspectionRecorder(settings.inspections_dir, inspection_name, label)
        install_inspector(browser.context, recorder)
        page = browser.navigate(target_url)
        print(f"Inspection file: {recorder.path}", flush=True)
        if snapshot:
            page.locator('header[data-testid="appTopbar"]').wait_for()
            page.wait_for_timeout(1000)
            print(f"Page structure: {capture_page_structure(page, settings.inspections_dir, inspection_name, label)}")
            return 0
        try:
            wait_for_inspections(page)
        except KeyboardInterrupt:
            print(f"\nInspection complete. Records: {recorder.path}")
    return 0


def _run(
    settings: Settings,
    apps: tuple[AppConfig, ...],
    actions: tuple[str, ...],
    concurrency: int | None,
) -> int:
    runner = ConcurrentRunner(
        settings,
        apps,
        concurrency=concurrency,
    )
    summary = asyncio.run(runner.run(actions))
    report_path = generate_excel_report(summary, settings.reports_dir)
    result = "SUCCESS" if summary.ok else "FAILED"
    print(f"Run {result}. Excel report: {report_path}")
    return 0 if summary.ok else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        configure_logging(settings, verbose=args.verbose)
        apps = load_apps(settings.apps_file)

        if args.command == "list-actions":
            print("\n".join(list_actions()))
            return 0
        if args.command == "list-apps":
            print("\n".join(app.name for app in apps))
            return 0
        if args.command == "inspect":
            return _open_and_inspect(settings, apps, args.app, args.url, args.snapshot, args.label)
        if args.command == "run":
            selected_apps = (
                (AppConfig(name="URL trial", url=_validate_airtable_url(args.url, "Run URL")),)
                if args.url else _select_apps(apps, args.apps)
            )
            return _run(
                settings,
                selected_apps,
                tuple(args.actions),
                args.concurrency,
            )
        raise AssertionError(f"Unhandled command: {args.command}")
    except (ValueError, BrowserLaunchError, NavigationError, PlaywrightError) as exc:
        LOGGER.error("%s", exc)
        return 1
    except Exception:
        LOGGER.exception("Unexpected fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
