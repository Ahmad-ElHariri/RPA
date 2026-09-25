"""Playwright and persistent Google Chrome lifecycle management."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import TracebackType

from playwright.async_api import (
    BrowserContext as AsyncBrowserContext,
    Error as AsyncPlaywrightError,
    Page as AsyncPage,
    Playwright as AsyncPlaywright,
    TimeoutError as AsyncPlaywrightTimeoutError,
    async_playwright,
)
from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from backend.config import Settings, lebanon_now


LOGGER = logging.getLogger(__name__)


class BrowserLaunchError(RuntimeError):
    """Raised when the persistent Chrome session cannot be started."""


class NavigationError(RuntimeError):
    """Raised when navigation does not reach a usable page."""


class BrowserSession:
    """Own a Playwright instance and one persistent Chrome context."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> "BrowserSession":
        self.settings.ensure_runtime_dirs()
        try:
            self._playwright = sync_playwright().start()
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.settings.chrome_profile_dir,
                channel="chrome",
                headless=self.settings.headless,
                slow_mo=self.settings.slow_mo_ms,
                viewport=None,
                args=["--start-maximized"],
            )
            self.context.set_default_timeout(self.settings.browser_timeout_ms)
            self.context.set_default_navigation_timeout(
                self.settings.navigation_timeout_ms
            )
            return self
        except PlaywrightError as exc:
            self.close()
            raise BrowserLaunchError(
                "Could not launch Google Chrome. Ensure Chrome is installed and no "
                "other Chrome process is using this automation profile."
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def page(self) -> Page:
        if self.context is None:
            raise RuntimeError("BrowserSession has not been started")
        open_pages = [page for page in self.context.pages if not page.is_closed()]
        return open_pages[0] if open_pages else self.context.new_page()

    def navigate(self, url: str) -> Page:
        page = self.page
        LOGGER.info("Opening configured Airtable page")
        try:
            page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as exc:
            raise NavigationError(
                "Airtable did not finish its initial navigation before the timeout"
            ) from exc
        except PlaywrightError as exc:
            raise NavigationError("Chrome could not navigate to the Airtable URL") from exc
        return page

    def screenshot(self, page: Page, label: str) -> Path | None:
        """Capture a best-effort diagnostic screenshot without masking an error."""
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "failure"
        timestamp = lebanon_now().strftime("%Y-%m-%d__%H-%M-%S")
        path = self.settings.screenshots_dir / f"{timestamp}-{safe_label}.png"
        try:
            page.screenshot(path=path, full_page=True)
        except (PlaywrightError, OSError):
            LOGGER.exception("Could not capture diagnostic screenshot")
            return None
        LOGGER.info("Saved diagnostic screenshot to %s", path)
        return path

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except PlaywrightError:
                LOGGER.exception("Error while closing the browser context")
            finally:
                self.context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None


class AsyncBrowserSession:
    """Own one persistent Chrome context shared by concurrent async pages."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: AsyncPlaywright | None = None
        self.context: AsyncBrowserContext | None = None

    async def __aenter__(self) -> "AsyncBrowserSession":
        self.settings.ensure_runtime_dirs()
        try:
            self._playwright = await async_playwright().start()
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.settings.chrome_profile_dir,
                channel="chrome",
                headless=self.settings.headless,
                slow_mo=self.settings.slow_mo_ms,
                viewport=None,
                args=["--start-maximized"],
            )
            self.context.set_default_timeout(self.settings.browser_timeout_ms)
            self.context.set_default_navigation_timeout(
                self.settings.navigation_timeout_ms
            )
            return self
        except (AsyncPlaywrightError, OSError) as exc:
            await self.close()
            raise BrowserLaunchError(
                "Could not launch Google Chrome. Ensure Chrome is installed and no "
                "other Chrome process is using this automation profile."
            ) from exc

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def new_page(self) -> AsyncPage:
        if self.context is None:
            raise RuntimeError("AsyncBrowserSession has not been started")
        return await self.context.new_page()

    async def navigate(self, page: AsyncPage, url: str) -> None:
        LOGGER.info("Opening Airtable app in page %s", id(page))
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except AsyncPlaywrightTimeoutError as exc:
            raise NavigationError(
                "Airtable did not finish its initial navigation before the timeout"
            ) from exc
        except AsyncPlaywrightError as exc:
            raise NavigationError("Chrome could not navigate to the Airtable URL") from exc

    async def screenshot(self, page: AsyncPage, label: str) -> Path | None:
        """Capture a best-effort diagnostic screenshot for an async job."""
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "failure"
        timestamp = lebanon_now().strftime("%Y-%m-%d__%H-%M-%S")
        path = self.settings.screenshots_dir / f"{timestamp}-{safe_label}.png"
        try:
            await page.screenshot(path=path, full_page=True)
        except (AsyncPlaywrightError, OSError):
            LOGGER.exception("Could not capture diagnostic screenshot")
            return None
        LOGGER.info("Saved diagnostic screenshot to %s", path)
        return path

    async def close(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            except AsyncPlaywrightError:
                LOGGER.exception("Error while closing the async browser context")
            finally:
                self.context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
