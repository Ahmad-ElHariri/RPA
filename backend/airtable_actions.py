"""Shared action registry, result models, and element validation."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from importlib import import_module
from typing import Any

from playwright.async_api import ElementHandle, Error as PlaywrightError, Locator, Page

from backend.config import Settings, lebanon_now


class Outcome(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True)
class CandidateResult:
    candidate: str
    outcome: Outcome
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ActionReport:
    action: str
    target_url: str
    started_at: str
    results: list[CandidateResult] = field(default_factory=list)
    final_url: str | None = None
    finished_at: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(
            item.outcome is not Outcome.FAILED for item in self.results
        )

    def finish(self, page: Page | None = None) -> "ActionReport":
        if page is not None:
            self.final_url = page.url
        self.finished_at = _now()
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


Action = Callable[[Page, Settings], Awaitable[ActionReport]]


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    function: Action
    writes_data: bool


_ACTIONS: dict[str, ActionDefinition] = {}
_OLD_ACTIONS: dict[str, ActionDefinition] = {}


class _CandidateError(RuntimeError):
    pass


def _now() -> str:
    return lebanon_now().isoformat()


def _register(*, writes_data: bool) -> Callable[[Action], Action]:
    def decorator(action: Action) -> Action:
        registry = (
            _OLD_ACTIONS
            if action.__module__ == "backend.old_core_global_changes"
            else _ACTIONS
        )
        if action.__name__ in registry:
            raise ValueError(f"Duplicate action name: {action.__name__}")
        registry[action.__name__] = ActionDefinition(action, writes_data)
        return action

    return decorator


def _new_report(action: str, page: Page) -> ActionReport:
    return ActionReport(action=action, target_url=page.url, started_at=_now())


def failed_action_report(action: str, url: str, message: str) -> ActionReport:
    """Create a report for a URL-level failure before an action can complete."""
    report = ActionReport(action=action, target_url=url, started_at=_now())
    report.results.append(
        CandidateResult(candidate="URL", outcome=Outcome.FAILED, message=message)
    )
    return report.finish()


async def _require_one(
    page: Page,
    locator: Locator,
    description: str,
    timeout_ms: int,
    *,
    exact_text: str | None = None,
) -> ElementHandle:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        candidates: list[ElementHandle] = []
        for handle in await locator.element_handles():
            try:
                normalized_text = " ".join((await handle.inner_text()).split())
                text_matches = exact_text is None or normalized_text == exact_text
                if (
                    await handle.is_visible()
                    and await handle.is_enabled()
                    and text_matches
                ):
                    candidates.append(handle)
            except PlaywrightError:
                continue

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise _CandidateError(
                f"Found {len(candidates)} visible, enabled candidates for {description}; "
                "refusing to choose by position."
            )
        if time.monotonic() >= deadline:
            raise _CandidateError(f"Could not find a usable {description}.")
        await page.wait_for_timeout(250)


def _registry(old: bool) -> dict[str, ActionDefinition]:
    import_module("backend.old_core_global_changes" if old else "functions")
    return _OLD_ACTIONS if old else _ACTIONS


def list_actions(*, old: bool = False) -> tuple[str, ...]:
    """Return new actions by default, or preserved legacy actions for the CLI."""
    return tuple(sorted(_registry(old)))


async def run_action(
    name: str, page: Page, settings: Settings, *, old: bool = False
) -> ActionReport:
    """Run a named action without coupling callers to its implementation."""
    registry = _registry(old)
    try:
        definition = registry[name]
    except KeyError as exc:
        available = ", ".join(list_actions(old=old)) or "none"
        raise ValueError(f"Unknown action {name!r}. Available actions: {available}") from exc
    return await definition.function(page, settings)


def action_writes_data(name: str, *, old: bool = False) -> bool:
    """Return whether an action can modify Airtable state."""
    registry = _registry(old)
    try:
        return registry[name].writes_data
    except KeyError as exc:
        raise ValueError(f"Unknown action {name!r}") from exc
