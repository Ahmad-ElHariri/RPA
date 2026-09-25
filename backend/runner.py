"""Controlled concurrent execution across named Airtable apps."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, Page

from backend.airtable_actions import (
    ActionReport,
    action_writes_data,
    failed_action_report,
    run_action,
)
from backend.browser import AsyncBrowserSession, BrowserLaunchError, NavigationError
from backend.config import lebanon_now, AppConfig, Settings


LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return lebanon_now().isoformat()


@dataclass(slots=True)
class AppActionExecution:
    app: AppConfig
    action: str
    report: ActionReport
    duration_seconds: float
    screenshot_path: Path | None = None


@dataclass(slots=True)
class RunSummary:
    actions: tuple[str, ...]
    concurrency: int
    started_at: str
    executions: list[AppActionExecution] = field(default_factory=list)
    finished_at: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.executions) and all(
            execution.report.ok for execution in self.executions
        )

    def finish(self) -> "RunSummary":
        self.finished_at = _now()
        return self


class ConcurrentRunner:
    """Run each app in one worker using its own page in a shared context.

    An app is a single queue job. All requested actions for that app are executed
    serially, which guarantees that two write actions never modify the same app
    simultaneously. Separate app jobs may run concurrently.
    """

    def __init__(
        self,
        settings: Settings,
        apps: tuple[AppConfig, ...],
        *,
        concurrency: int | None = None,
        old: bool = False,
    ) -> None:
        self.settings = settings
        self.apps = apps
        self.old = old
        self.concurrency = settings.concurrency if concurrency is None else concurrency
        if self.concurrency <= 0:
            raise ValueError("Concurrency must be greater than zero")

    async def run(self, actions: tuple[str, ...]) -> RunSummary:
        if not actions:
            raise ValueError("At least one action is required")
        if len(actions) != len(set(actions)):
            raise ValueError("The same action cannot be requested twice in one run")
        for action in actions:
            action_writes_data(action, old=self.old)  # Validate before Chrome launch.

        summary = RunSummary(
            actions=actions,
            concurrency=min(self.concurrency, len(self.apps)),
            started_at=_now(),
        )
        if not self.apps:
            return summary.finish()

        try:
            async with AsyncBrowserSession(self.settings) as browser:
                await self._run_workers(browser, actions, summary)
        except BrowserLaunchError as exc:
            LOGGER.exception("The shared Chrome session could not be started")
            for app in self.apps:
                for action in actions:
                    summary.executions.append(
                        AppActionExecution(
                            app=app,
                            action=action,
                            report=failed_action_report(action, app.url, str(exc)),
                            duration_seconds=0.0,
                        )
                    )

        app_order = {app.name: index for index, app in enumerate(self.apps)}
        action_order = {action: index for index, action in enumerate(actions)}
        summary.executions.sort(
            key=lambda item: (app_order[item.app.name], action_order[item.action])
        )
        return summary.finish()

    async def _run_workers(
        self,
        browser: AsyncBrowserSession,
        actions: tuple[str, ...],
        summary: RunSummary,
    ) -> None:
        queue: asyncio.Queue[AppConfig] = asyncio.Queue()
        for app in self.apps:
            queue.put_nowait(app)

        worker_count = min(self.concurrency, len(self.apps))

        async def worker(worker_number: int) -> None:
            while True:
                try:
                    app = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    LOGGER.info("Worker %d started app %s", worker_number, app.name)
                    await self._run_app(browser, app, actions, summary)
                except Exception:
                    # A worker-level guard ensures an unexpected app failure cannot
                    # cancel other workers or leave this app without report rows.
                    LOGGER.exception("Worker %d failed app %s", worker_number, app.name)
                    existing = {
                        item.action
                        for item in summary.executions
                        if item.app.name == app.name
                    }
                    for action in actions:
                        if action not in existing:
                            summary.executions.append(
                                AppActionExecution(
                                    app=app,
                                    action=action,
                                    report=failed_action_report(
                                        action,
                                        app.url,
                                        "Unexpected worker failure; see terminal output.",
                                    ),
                                    duration_seconds=0.0,
                                )
                            )
                finally:
                    queue.task_done()

        await asyncio.gather(
            *(worker(number) for number in range(1, worker_count + 1))
        )

    async def _run_app(
        self,
        browser: AsyncBrowserSession,
        app: AppConfig,
        actions: tuple[str, ...],
        summary: RunSummary,
    ) -> None:
        page: Page | None = None
        try:
            page = await browser.new_page()
            for action in actions:
                if page.is_closed():
                    page = await browser.new_page()
                execution = await self._run_one(browser, page, app, action)
                summary.executions.append(execution)
        except Exception:
            LOGGER.exception("Could not create or maintain a page for app %s", app.name)
            completed = {
                item.action for item in summary.executions if item.app.name == app.name
            }
            for action in actions:
                if action not in completed:
                    summary.executions.append(
                        AppActionExecution(
                            app=app,
                            action=action,
                            report=failed_action_report(
                                action,
                                app.url,
                                "The app page failed; see terminal output.",
                            ),
                            duration_seconds=0.0,
                        )
                    )
        finally:
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except PlaywrightError:
                    LOGGER.exception("Could not close the page for app %s", app.name)

    async def _run_one(
        self,
        browser: AsyncBrowserSession,
        page: Page,
        app: AppConfig,
        action: str,
    ) -> AppActionExecution:
        started = time.perf_counter()
        screenshot_path: Path | None = None
        LOGGER.info("Running %s for app %s", action, app.name)
        try:
            await browser.navigate(page, app.url)
            report = await run_action(action, page, self.settings, old=self.old)
            report.target_url = app.url
            report.final_url = page.url
        except (NavigationError, PlaywrightError) as exc:
            LOGGER.exception("Playwright failure for app %s, action %s", app.name, action)
            report = failed_action_report(
                action, app.url, f"The app could not be processed: {exc}"
            )
        except Exception as exc:
            LOGGER.exception("Unexpected failure for app %s, action %s", app.name, action)
            report = failed_action_report(
                action,
                app.url,
                f"Unexpected {type(exc).__name__}; see terminal output.",
            )

        if not report.ok and not page.is_closed():
            screenshot_path = await browser.screenshot(page, f"{app.name}-{action}-failed")
        duration = time.perf_counter() - started
        LOGGER.info(
            "Finished %s for app %s in %.2fs (%s)",
            action,
            app.name,
            duration,
            "ok" if report.ok else "failed",
        )
        return AppActionExecution(
            app=app,
            action=action,
            report=report,
            duration_seconds=duration,
            screenshot_path=screenshot_path,
        )
