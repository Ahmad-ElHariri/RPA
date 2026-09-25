"""Registered Airtable business workflows."""
from __future__ import annotations
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse
from playwright.async_api import ElementHandle, Error as PlaywrightError, Frame, Locator, Page
from backend.config import Settings
from backend.airtable_actions import (
    ActionReport, CandidateResult, Outcome, _CandidateError,
    _new_report, _register, _require_one,
)

# 1. CORE workflow targets: approval emails and Smart Import configuration.
_AUTOMATION_NAME = "[CORE] Send for Approval"


_EMAIL_ACTION_NAME = "Send an email"


_EMAIL_ACTION_DESCRIPTIONS = (
    "Email to original Briefer (If different to Planner)",
    "Email to Alt Approver",
)


_EMAIL_BODY_MARKERS = ("The media plan for", "View Media Plan")


_SIGNATURE = "Publicis x P&G Media Team"


_SMART_IMPORT_EXTRA_FIELDS = (
    ("CPC", "number"),
    ("CPM", "number"),
    ("CPA", "number"),
    ("CPV", "number"),
    ("CPE", "number"),
    ("CPI", "number"),
    ("CPCV", "number"),
    ("CPL", "number"),
    ("CPUR", "number"),
    ("CTR", "number"),
    ("VTR", "number"),
    ("Campaign Name Reference", "text"),
    ("Start Date", "date"),
    ("End Date", "date"),
    ("Objective (Core)", "text"),
    ("Audience", "text"),
    ("Creative Details", "text"),
    ("Budget Split Details", "text"),
    ("Budget", "number"),
)


_SMART_IMPORT_LINKED_TABLES = (
    "Brand",
    "Sub-Brand",
    "Markets",
    "Buying Model",
    "Platforms",
    "Language",
)


_SMART_IMPORT_INLINE_EDIT_TABLES = (
    "Field Mapping Sessions",
    "\N{LOCK} Sync Control",
    "Mapping",
    "Smart Import Field Mappings",
    *_SMART_IMPORT_LINKED_TABLES,
)


_SMART_IMPORT_INSTALLATION_TEST_ID = (
    "page-element:blockInstallationInQueryContainer"
)


_SMART_IMPORT_INSTALLATION_CLICKS = 1


_SMART_IMPORT_SECTION_RETRIES = 2


_MEDIA_PLAN_SECTIONS = (
    ("Add/Edit Media Plans", "page-element:grid"),
    ("Review Media Plans", "page-element:levels"),
)


_SUGGESTED_METRIC = "Suggested Metric"


# 2. Smart Import helpers: discover the frame, fields, tables, and switches.
async def _wait_for_smart_import_frame(page: Page, timeout_ms: int) -> Frame:
    """Find the extension iframe by origin and its visible product heading."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for frame in page.frames:
            if "airtableblocks.com/__runFrame" not in frame.url:
                continue
            try:
                body = frame.locator("body")
                if await body.count() != 1:
                    continue
                normalized_text = " ".join((await body.inner_text()).split())
                if "Smart Import CORE" in normalized_text:
                    return frame
            except PlaywrightError:
                continue
        if time.monotonic() >= deadline:
            raise _CandidateError(
                "Could not find the loaded Smart Import CORE extension frame."
            )
        await page.wait_for_timeout(250)


def _smart_import_option_text_matches(
    text: str, field_name: str, field_type: str
) -> bool:
    # Tolerate the mojibake form found in some inspection output, but compare the
    # complete normalized row so fields such as CPC and CPCV cannot be confused.
    normalized = " ".join(text.replace("Â·", "·").split())
    return normalized == f"{field_name} · {field_type}"


async def _smart_import_field_is_configured(
    extra_fields_section: Locator, field_name: str, field_type: str
) -> bool:
    expected_text = f"{field_name} {field_type}"
    for text in await extra_fields_section.locator("*").all_inner_texts():
        if not text:
            continue
        normalized = " ".join(text.split())
        if normalized == expected_text or normalized.startswith(
            f"{expected_text} "
        ):
            return True
    return False


async def _click_smart_import_field(
    page: Page,
    frame: Frame,
    field_name: str,
    field_type: str,
    timeout_ms: int,
) -> bool:
    """Add one extra field, returning False when it is already configured."""
    extra_fields_section = frame.get_by_text(
        "Extra mappable fields", exact=True
    ).locator(
        "xpath=ancestor::*[.//input[@role='combobox']][1]"
    )
    if await _smart_import_field_is_configured(
        extra_fields_section, field_name, field_type
    ):
        return False

    combobox = await _require_one(
        page,
        extra_fields_section.get_by_role("combobox"),
        "Smart Import CORE 'Add a field' combobox",
        timeout_ms,
    )
    await combobox.click()
    await combobox.fill(field_name)
    option_locator = frame.locator('li[role="option"]')
    deadline = time.monotonic() + timeout_ms / 1000

    while True:
        candidates: list[ElementHandle] = []
        for handle in await option_locator.element_handles():
            try:
                text = await handle.inner_text()
                if (
                    _smart_import_option_text_matches(
                        text, field_name, field_type
                    )
                    and await handle.is_visible()
                    and await handle.is_enabled()
                ):
                    candidates.append(handle)
            except PlaywrightError:
                continue

        if len(candidates) == 1:
            await candidates[0].click()
            return True
        elif len(candidates) > 1:
            raise _CandidateError(
                f"Found {len(candidates)} usable options for Smart Import CORE "
                f"field {field_name!r} ({field_type}); refusing to choose by position."
            )

        if time.monotonic() >= deadline:
            action = "find"
            raise _CandidateError(
                f"Could not {action} Smart Import CORE field "
                f"{field_name!r} ({field_type})."
            )
        await page.wait_for_timeout(250)


async def _page_table_is_added(table: ElementHandle) -> bool | None:
    """Read Airtable's Show/Hide action without toggling the table."""
    try:
        action_description = await table.eval_on_selector(
            "[aria-description]",
            "element => element.getAttribute('aria-description')",
        )
    except PlaywrightError:
        return None
    if not isinstance(action_description, str):
        return None
    normalized = action_description.casefold()
    if "hide" in normalized:
        return True
    if "show" in normalized:
        return False
    return None


async def _wait_for_interface_save(page: Page, timeout_ms: int) -> None:
    """Wait until Interface Designer reports that the table change is saved."""
    await page.wait_for_timeout(500)
    indicator = page.get_by_test_id("globalStatusIndicator")
    visible_indicators = [
        handle for handle in await indicator.element_handles()
        if await handle.is_visible()
    ]
    if len(visible_indicators) > 1:
        raise _CandidateError(
            "Found multiple visible Interface Designer status indicators."
        )
    if len(visible_indicators) == 1:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            normalized = " ".join((await indicator.inner_text()).split())
            if (
                normalized == "All changes saved"
                or normalized.startswith("No changes")
                or normalized.startswith("Interface has unpublished changes")
            ):
                return
            if time.monotonic() >= deadline:
                break
            await page.wait_for_timeout(250)
    status = page.get_by_text("All changes saved", exact=True)
    if not any([
        await handle.is_visible() for handle in await status.element_handles()
    ]):
        status = page.get_by_text(re.compile(
            r"^No changes(?:\s*[·•-]\s*Last published.*)?$"
        ))
    await _require_one(
        page,
        status,
        "Interface Designer saved status ('All changes saved' or 'No changes')",
        timeout_ms,
    )


async def _smart_import_table(
    page: Page, table_name: str, timeout_ms: int
) -> ElementHandle:
    """Find a table in the popover, opening it when necessary."""
    dialog = page.get_by_test_id("popover:Table")
    dialog_is_open = False
    for handle in await dialog.element_handles():
        try:
            if await handle.is_visible():
                dialog_is_open = True
                break
        except PlaywrightError:
            continue

    if not dialog_is_open:
        sidebar = page.get_by_test_id("page-creator-sidebar")
        open_popover = await _require_one(
            page,
            sidebar.get_by_role(
                "button", name="open Table popover", exact=True
            ),
            "Interface Designer open Table popover button",
            timeout_ms,
        )
        await open_popover.click()

    search = await _require_one(
        page,
        dialog.get_by_role("textbox"),
        "Table popover search input",
        timeout_ms,
    )
    await search.fill(table_name)
    table = await _require_one(
        page,
        dialog.get_by_role("listitem"),
        f"Table dialog item {table_name!r}",
        timeout_ms,
        exact_text=table_name,
    )
    return table


async def _ensure_smart_import_table(
    page: Page, table_name: str, timeout_ms: int
) -> bool:
    """Apply one table change; final verification belongs to the section."""
    table = await _smart_import_table(page, table_name, timeout_ms)
    is_added = await _page_table_is_added(table)
    if is_added is None:
        raise _CandidateError(
            f"Could not determine whether table {table_name!r} is already added."
        )
    if is_added:
        return False
    await table.click()
    return True


async def _inline_editing_toggle(
    page: Page, table_name: str, timeout_ms: int
) -> ElementHandle:
    """Find the inline-editing switch for one table."""
    table_row = page.get_by_text(table_name, exact=True).locator(
        "xpath=ancestor::*[.//input[@role='switch'] "
        "and .//span[normalize-space()='Edit records inline']][1]"
    )
    toggle = await _require_one(
        page,
        table_row.get_by_role("switch"),
        f"Edit records inline switch for {table_name!r}",
        timeout_ms,
    )
    return toggle


async def _ensure_inline_editing(
    page: Page, table_name: str, timeout_ms: int
) -> bool:
    """Apply one inline-editing change without waiting for individual saves."""
    toggle = await _inline_editing_toggle(page, table_name, timeout_ms)
    if await toggle.is_checked():
        return False
    await toggle.click()
    return True


# 3. Section validation: verify all targets and retry only unresolved items.
async def _run_validated_section(
    page: Page,
    report: ActionReport,
    section: str,
    items: tuple[str, ...],
    apply_item: Callable[[str], Awaitable[bool]],
    validate_item: Callable[[str], Awaitable[bool]],
    timeout_ms: int,
    *,
    wait_for_save: bool = False,
) -> bool:
    """Apply a full section, then validate all targets and repair only failures."""
    pending = list(items)
    changed: set[str] = set()
    attempts = dict.fromkeys(items, 0)
    errors: dict[str, str] = {}
    for _ in range(_SMART_IMPORT_SECTION_RETRIES + 1):
        for item in pending:
            attempts[item] += 1
            try:
                if await apply_item(item):
                    changed.add(item)
            except (_CandidateError, PlaywrightError) as exc:
                errors[item] = str(exc)

        save_error = None
        if wait_for_save:
            try:
                await _wait_for_interface_save(page, timeout_ms)
            except (_CandidateError, PlaywrightError) as exc:
                save_error = str(exc)

        # Poll whole-section snapshots for asynchronous state updates. No writes
        # occur during validation, including when an earlier item regresses.
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            pending = []
            for item in items:
                try:
                    correct = await validate_item(item)
                    if correct and save_error is None:
                        errors.pop(item, None)
                    else:
                        pending.append(item)
                        errors[item] = save_error or "Expected final state did not persist."
                except (_CandidateError, PlaywrightError) as exc:
                    pending.append(item)
                    errors[item] = str(exc)
            if not pending or time.monotonic() >= deadline or save_error:
                break
            await page.wait_for_timeout(250)
        if not pending:
            break

    for item in items:
        failed = item in pending
        report.results.append(
            CandidateResult(
                candidate=f"{section}: {item}",
                outcome=(Outcome.FAILED if failed else
                         Outcome.SUCCESS if item in changed else Outcome.SKIPPED),
                message=(
                    f"Could not apply {item!r}: {errors[item]}"
                    if failed else
                    f"Applied and validated {item!r}." if item in changed else
                    f"{item!r} is already correct; section validated."
                ),
                details={"attempts": attempts[item], "section": section},
            )
        )
    report.results.append(
        CandidateResult(
            candidate=f"{section} section validation",
            outcome=Outcome.FAILED if pending else Outcome.SUCCESS,
            message=(
                f"Section FAILED after {_SMART_IMPORT_SECTION_RETRIES + 1} attempts. "
                f"Unresolved items: {', '.join(pending)}."
                if pending else "Entire expected section state validated."
            ),
            details={"section": section, "unresolved_items": pending.copy()},
        )
    )
    return not pending


# 4. Email helpers: identify the selected action and remove only the exact signature.
async def _wait_for_email_editors(
    page: Page, timeout_ms: int
) -> list[ElementHandle]:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        editors: list[ElementHandle] = []
        for handle in await page.locator(
            '[contenteditable="true"]'
        ).element_handles():
            try:
                text = (await handle.inner_text()).strip()
                if await handle.is_visible() and all(
                    marker in text for marker in _EMAIL_BODY_MARKERS
                ):
                    editors.append(handle)
            except PlaywrightError:
                continue
        if editors:
            return editors
        if time.monotonic() >= deadline:
            raise _CandidateError(
                "The expected email body editor was not found after opening the action."
            )
        await page.wait_for_timeout(250)


async def _wait_for_selected_action(
    page: Page,
    action_card: ElementHandle,
    expected_description: str,
    timeout_ms: int,
) -> None:
    """Verify Airtable marks the clicked workflow node as the focused action."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            is_focused = await action_card.evaluate(
                "element => Boolean(element.closest('[data-testid=\"focused-workflow-node\"]'))"
            )
            if is_focused:
                return
        except PlaywrightError:
            pass
        if time.monotonic() >= deadline:
            raise _CandidateError(
                f"Airtable did not mark {expected_description!r} as the selected action."
            )
        await page.wait_for_timeout(250)


async def _paragraph_texts(editor: ElementHandle) -> list[str]:
    return await editor.eval_on_selector_all(
        "p",
        "elements => elements.map(e => (e.innerText || e.textContent || '')"
        ".replace(/\\s+/g, ' ').trim()).filter(Boolean)",
    )


async def _remove_signature_from_editor(
    page: Page,
    editor: ElementHandle,
    candidate_name: str,
    settings: Settings,
) -> CandidateResult:
    paragraphs = await _paragraph_texts(editor)
    if not paragraphs:
        return CandidateResult(
            candidate=candidate_name,
            outcome=Outcome.FAILED,
            message="The expected email editor contains no paragraph text.",
        )

    exact_matches = [text for text in paragraphs if text == _SIGNATURE]
    if not exact_matches:
        if _SIGNATURE in await editor.inner_text():
            return CandidateResult(
                candidate=candidate_name,
                outcome=Outcome.FAILED,
                message="The signature exists, but not as an exact standalone paragraph.",
            )
        return CandidateResult(
            candidate=candidate_name,
            outcome=Outcome.SKIPPED,
            message="The signature is already absent.",
        )

    if len(exact_matches) != 1 or paragraphs[-1] != _SIGNATURE:
        return CandidateResult(
            candidate=candidate_name,
            outcome=Outcome.FAILED,
            message="The signature was found in an unexpected position or more than once.",
            details={"exact_occurrences": len(exact_matches), "last_line": paragraphs[-1]},
        )

    target_paragraphs: list[ElementHandle] = []
    for paragraph in await editor.query_selector_all("p"):
        text = " ".join((await paragraph.inner_text()).split())
        if text == _SIGNATURE:
            target_paragraphs.append(paragraph)
    if len(target_paragraphs) != 1:
        return CandidateResult(
            candidate=candidate_name,
            outcome=Outcome.FAILED,
            message="The validated signature paragraph changed before editing.",
        )

    await target_paragraphs[0].evaluate(
        """element => {
            element.scrollIntoView({block: 'center'});
            element.closest('[contenteditable="true"]')?.focus();
            const range = document.createRange();
            range.selectNodeContents(element);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    await page.keyboard.press("Backspace")
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(min(1_000, settings.browser_timeout_ms))

    if _SIGNATURE in await editor.inner_text():
        return CandidateResult(
            candidate=candidate_name,
            outcome=Outcome.FAILED,
            message="The deletion was attempted, but verification still found the signature.",
        )
    return CandidateResult(
        candidate=candidate_name,
        outcome=Outcome.SUCCESS,
        message="Removed and verified the final-line signature.",
    )


# 5. Smart Import CORE: configure fields, linked tables, and inline editing.
@_register(writes_data=True)
async def smart_import_core(page: Page, settings: Settings) -> ActionReport:
    """Run the inspected Smart Import CORE setup clicks in their captured order."""
    action_name = "smart_import_core"
    report = _new_report(action_name, page)

    try:
        # 5.1. Open the extension and verify its setup panel before configuring it.
        async def open_installation(_: str) -> bool:
            # Opening the extension is navigation; completion is established by
            # the loaded extension frame, rather than a successful click.
            for click_number in range(1, _SMART_IMPORT_INSTALLATION_CLICKS + 1):
                installation = page.get_by_test_id(_SMART_IMPORT_INSTALLATION_TEST_ID)
                button = await _require_one(
                    page, installation.get_by_role("button"),
                    f"Smart Import installation button (click {click_number})",
                    settings.browser_timeout_ms,
                )
                await button.click()
                if click_number < _SMART_IMPORT_INSTALLATION_CLICKS:
                    await page.wait_for_timeout(250)
            return True

        async def installation_is_loaded(_: str) -> bool:
            await _wait_for_smart_import_frame(page, settings.browser_timeout_ms)
            return True

        if not await _run_validated_section(
            page, report, "Installation", ("Smart Import CORE frame",),
            open_installation, installation_is_loaded, settings.browser_timeout_ms,
        ):
            return report.finish(page)

        frame = await _wait_for_smart_import_frame(page, settings.browser_timeout_ms)

        async def open_setup(_: str) -> bool:
            setup = await _require_one(
                page, frame.get_by_role("button", name="Setup", exact=True),
                "Smart Import CORE Setup button", settings.browser_timeout_ms,
                exact_text="Setup",
            )
            await setup.click()
            return True

        async def setup_is_open(_: str) -> bool:
            await _require_one(
                page, frame.get_by_text("Extra mappable fields", exact=True),
                "Setup extra mappable fields heading", settings.browser_timeout_ms,
            )
            return True

        if not await _run_validated_section(
            page, report, "Setup", ("Extra mappable fields panel",),
            open_setup, setup_is_open, settings.browser_timeout_ms,
        ):
            return report.finish(page)

        # 5.2. Apply and validate all expected fields and linked tables.
        field_types = dict(_SMART_IMPORT_EXTRA_FIELDS)
        extra_fields_section = frame.get_by_text(
            "Extra mappable fields", exact=True
        ).locator("xpath=ancestor::*[.//input[@role='combobox']][1]")
        if not await _run_validated_section(
            page, report, "Extra field", tuple(field_types),
            lambda name: _click_smart_import_field(
                page, frame, name, field_types[name], settings.browser_timeout_ms
            ),
            lambda name: _smart_import_field_is_configured(
                extra_fields_section, name, field_types[name]
            ),
            settings.browser_timeout_ms,
        ):
            return report.finish(page)

        async def table_is_added(name: str) -> bool:
            table = await _smart_import_table(page, name, settings.browser_timeout_ms)
            return await _page_table_is_added(table) is True

        if not await _run_validated_section(
            page, report, "Linked table", _SMART_IMPORT_LINKED_TABLES,
            lambda name: _ensure_smart_import_table(
                page, name, settings.browser_timeout_ms
            ),
            table_is_added, settings.browser_timeout_ms, wait_for_save=True,
        ):
            return report.finish(page)

        # 5.3. Enable inline editing and verify the complete per-table state.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        sidebar = page.get_by_test_id("page-creator-sidebar")
        async def open_inline_configuration(_: str) -> bool:
            configure = await _require_one(
                page,
                sidebar.get_by_role(
                    "button", name="Configure: Edit records inline by table", exact=True
                ),
                "Configure: Edit records inline by table button",
                settings.browser_timeout_ms,
            )
            await configure.click()
            return True

        async def inline_configuration_is_open(_: str) -> bool:
            await _inline_editing_toggle(
                page, "Field Mapping Sessions", settings.browser_timeout_ms
            )
            return True

        if not await _run_validated_section(
            page, report, "Inline editing configuration", ("Per-table switches",),
            open_inline_configuration, inline_configuration_is_open,
            settings.browser_timeout_ms,
        ):
            return report.finish(page)

        async def inline_editing_is_enabled(name: str) -> bool:
            toggle = await _inline_editing_toggle(
                page, name, settings.browser_timeout_ms
            )
            return await toggle.is_checked()

        if not await _run_validated_section(
            page, report, "Inline editing", _SMART_IMPORT_INLINE_EDIT_TABLES,
            lambda name: _ensure_inline_editing(
                page, name, settings.browser_timeout_ms
            ),
            inline_editing_is_enabled, settings.browser_timeout_ms,
            wait_for_save=True,
        ):
            return report.finish(page)
    except (_CandidateError, PlaywrightError) as exc:
        report.results.append(
            CandidateResult(
                candidate="Smart Import CORE setup sequence",
                outcome=Outcome.FAILED,
                message=str(exc),
                details={"stage_url": page.url},
            )
        )

    return report.finish(page)


# 6. Approval emails: remove and verify the Publicis signature in both target actions.
@_register(writes_data=True)
async def remove_publicis_media_team_signature(
    page: Page, settings: Settings
) -> ActionReport:
    """Remove the exact Publicis signature from both approval email bodies."""
    action_name = "remove_publicis_media_team_signature"
    report = _new_report(action_name, page)

    try:
        topbar = page.locator('header[data-testid="appTopbar"]')
        automations_link = await _require_one(
            page,
            topbar.get_by_role("link", name="Automations", exact=True),
            "Automations top-bar link",
            settings.browser_timeout_ms,
        )
        await automations_link.click()

        automation = await _require_one(
            page,
            page.get_by_role("button", name=_AUTOMATION_NAME, exact=True),
            f"automation named {_AUTOMATION_NAME!r}",
            settings.browser_timeout_ms,
        )
        await automation.click()

        for description in _EMAIL_ACTION_DESCRIPTIONS:
            email_action_locator = page.get_by_role("button").filter(
                has_text=_EMAIL_ACTION_NAME
            ).filter(has_text=description)
            email_action = await _require_one(
                page,
                email_action_locator,
                f"{_EMAIL_ACTION_NAME!r} action card for {description!r}",
                settings.browser_timeout_ms,
                exact_text=f"{_EMAIL_ACTION_NAME} {description}",
            )
            await email_action.click()
            await _wait_for_selected_action(
                page, email_action, description, settings.browser_timeout_ms
            )

            editors = await _wait_for_email_editors(
                page, settings.browser_timeout_ms
            )
            for index, editor in enumerate(editors, start=1):
                report.results.append(
                    await _remove_signature_from_editor(
                        page,
                        editor,
                        f"{description} - email body {index}",
                        settings,
                    )
                )
    except (_CandidateError, PlaywrightError) as exc:
        report.results.append(
            CandidateResult(
                candidate="Airtable automation workflow",
                outcome=Outcome.FAILED,
                message=str(exc),
                details={"stage_url": page.url},
            )
        )

    return report.finish(page)


# 7. Suggested Metric helpers: open each inspected grid and read field visibility.
async def _open_flow_core_planning(page: Page, timeout_ms: int) -> bool:
    """Open Flow Core Planning and select its list visualization."""
    await page.locator('header[data-testid="appTopbar"]').wait_for(
        timeout=timeout_ms
    )
    await page.wait_for_timeout(1_000)
    navigation = page.get_by_test_id("PageNavigationLeftNav")
    flow_core = navigation.get_by_role("link", name="Flow Core", exact=True)
    await _require_one(
        page,
        flow_core,
        "Flow Core navigation link",
        timeout_ms,
        exact_text="Flow Core",
    )
    await flow_core.click()
    levels = page.get_by_test_id("page-element:levels")
    empty_state = levels.locator(":scope > div").filter(
        has_text=re.compile(
            r"^\s*No briefs found that match current filters\s*"
            r"Remove filters to see more results\s*$"
        )
    )
    await _require_one(
        page,
        empty_state,
        "Flow Core Planning list canvas",
        timeout_ms,
        exact_text=(
            "No briefs found that match current filters "
            "Remove filters to see more results"
        ),
    )
    await empty_state.click()
    return True


async def _planning_list_is_selected(page: Page, timeout_ms: int) -> bool:
    await _require_one(
        page,
        page.get_by_role(
            "button", name="Configure: Record detail pages", exact=True
        ),
        "Planning Record detail pages configuration button",
        timeout_ms,
    )
    return True


async def _open_flow_core_brief_details(page: Page, timeout_ms: int) -> bool:
    configure = await _require_one(
        page,
        page.get_by_role(
            "button", name="Configure: Record detail pages", exact=True
        ),
        "Planning Record detail pages configuration button",
        timeout_ms,
    )
    await configure.click()
    dialog = page.get_by_test_id("popover:Record detail pages")
    customize = await _require_one(
        page,
        dialog.get_by_role(
            "button",
            name="Customize Flow Core - Brief Details page",
            exact=True,
        ),
        "Flow Core - Brief Details customize button",
        timeout_ms,
    )
    await customize.click()
    await page.wait_for_timeout(1_500)
    return True


async def _brief_details_is_open(page: Page, timeout_ms: int) -> bool:
    for section_name, _ in _MEDIA_PLAN_SECTIONS:
        heading = page.get_by_role("heading", name=section_name, exact=True)
        await heading.scroll_into_view_if_needed(timeout=timeout_ms)
        await _require_one(
            page,
            heading,
            f"{section_name!r} section heading",
            timeout_ms,
            exact_text=section_name,
        )
    return True


async def _close_fields_popover(page: Page, timeout_ms: int) -> None:
    dialog = page.get_by_test_id("popover:Fields")
    for handle in await dialog.element_handles():
        if not await handle.is_visible():
            continue
        close = await _require_one(
            page,
            dialog.get_by_role("button", name="Close", exact=True),
            "Fields popover Close button",
            timeout_ms,
        )
        await close.click()
        return


async def _open_media_plan_fields(
    page: Page,
    section_name: str,
    visualization_test_id: str,
    timeout_ms: int,
) -> Locator:
    """Select one named visualization and filter its Fields popover."""
    await _close_fields_popover(page, timeout_ms)
    section = page.get_by_test_id("page-element:queryContainer").filter(
        has=page.get_by_role("heading", name=section_name, exact=True)
    )
    await section.scroll_into_view_if_needed(timeout=timeout_ms)
    selection_locator = section.get_by_test_id(
        visualization_test_id
    ).get_by_role("button", name="Select element", exact=True)
    await selection_locator.scroll_into_view_if_needed(timeout=timeout_ms)
    selection = await _require_one(
        page,
        selection_locator,
        f"{section_name!r} data visualization selection control",
        timeout_ms,
    )
    await selection.click()

    sidebar = page.get_by_test_id("page-creator-sidebar")
    fields_button = await _require_one(
        page,
        sidebar.get_by_role("button", name="open Fields popover", exact=True),
        f"{section_name!r} Fields settings button",
        timeout_ms,
    )
    await fields_button.click()
    dialog = page.get_by_test_id("popover:Fields")
    search = await _require_one(
        page,
        dialog.get_by_role("textbox"),
        f"{section_name!r} Fields search input",
        timeout_ms,
    )
    await search.fill(_SUGGESTED_METRIC.casefold())
    return dialog


async def _suggested_metric_state(
    page: Page, dialog: Locator, section_name: str, timeout_ms: int
) -> tuple[str, Locator]:
    row = dialog.get_by_role("listitem").filter(
        has_text=re.compile(r"^\s*Suggested Metric\s*$")
    )
    await _require_one(
        page,
        row,
        f"exact Suggested Metric field row in {section_name!r}",
        timeout_ms,
        exact_text=_SUGGESTED_METRIC,
    )
    await row.hover()
    await page.wait_for_timeout(100)
    hide = row.get_by_role(
        "button", name=f"Hide {_SUGGESTED_METRIC}", exact=True
    )
    show = row.get_by_role(
        "button", name=f"Show {_SUGGESTED_METRIC}", exact=True
    )
    hide_count, show_count = await hide.count(), await show.count()
    if (hide_count, show_count) == (1, 0):
        return "visible", row
    if (hide_count, show_count) == (0, 1):
        return "hidden", row
    raise _CandidateError(
        f"Could not determine Suggested Metric visibility in {section_name!r}: "
        f"found {hide_count} Hide controls and {show_count} Show controls."
    )


async def _ensure_suggested_metric_hidden(
    page: Page,
    section_name: str,
    visualization_test_id: str,
    timeout_ms: int,
) -> bool:
    dialog = await _open_media_plan_fields(
        page, section_name, visualization_test_id, timeout_ms
    )
    state, row = await _suggested_metric_state(
        page, dialog, section_name, timeout_ms
    )
    if state == "hidden":
        return False
    await row.hover()
    hide = await _require_one(
        page,
        row.get_by_role(
            "button", name=f"Hide {_SUGGESTED_METRIC}", exact=True
        ),
        f"Hide Suggested Metric control in {section_name!r}",
        timeout_ms,
    )
    await hide.click()
    return True


async def _suggested_metric_is_hidden(
    page: Page,
    section_name: str,
    visualization_test_id: str,
    timeout_ms: int,
) -> bool:
    dialog = await _open_media_plan_fields(
        page, section_name, visualization_test_id, timeout_ms
    )
    state, _ = await _suggested_metric_state(
        page, dialog, section_name, timeout_ms
    )
    return state == "hidden"


# 8. Hide Suggested Metric: update and reload-verify both Media Plan sections.
@_register(writes_data=True)
async def hide_suggested_metric(page: Page, settings: Settings) -> ActionReport:
    """Hide Suggested Metric in both Flow Core Brief Details media-plan grids."""
    action_name = "hide_suggested_metric"
    report = _new_report(action_name, page)
    timeout_ms = settings.browser_timeout_ms

    try:
        if not await _run_validated_section(
            page,
            report,
            "Flow Core Planning navigation",
            ("Planning list",),
            lambda _: _open_flow_core_planning(page, timeout_ms),
            lambda _: _planning_list_is_selected(page, timeout_ms),
            timeout_ms,
        ):
            return report.finish(page)

        if not await _run_validated_section(
            page,
            report,
            "Brief Details navigation",
            ("Flow Core - Brief Details",),
            lambda _: _open_flow_core_brief_details(page, timeout_ms),
            lambda _: _brief_details_is_open(page, timeout_ms),
            timeout_ms,
        ):
            return report.finish(page)

        section_test_ids = dict(_MEDIA_PLAN_SECTIONS)
        section_names = tuple(section_test_ids)
        if not await _run_validated_section(
            page,
            report,
            "Suggested Metric field",
            section_names,
            lambda name: _ensure_suggested_metric_hidden(
                page, name, section_test_ids[name], timeout_ms
            ),
            lambda name: _suggested_metric_is_hidden(
                page, name, section_test_ids[name], timeout_ms
            ),
            timeout_ms,
        ):
            return report.finish(page)

        await _close_fields_popover(page, timeout_ms)
        await page.reload(wait_until="domcontentloaded")
        await _brief_details_is_open(page, timeout_ms)
        await _run_validated_section(
            page,
            report,
            "Reload persistence",
            section_names,
            lambda _: _unchanged(),
            lambda name: _suggested_metric_is_hidden(
                page, name, section_test_ids[name], timeout_ms
            ),
            timeout_ms,
        )
    except (_CandidateError, PlaywrightError) as exc:
        report.results.append(
            CandidateResult(
                candidate="Flow Core Suggested Metric workflow",
                outcome=Outcome.FAILED,
                message=str(exc),
                details={"stage_url": page.url},
            )
        )

    return report.finish(page)


async def _unchanged() -> bool:
    """Represent a validation-only item in the shared section runner."""
    return False


# 9. Delete button helpers: open its detail-page settings and inspect visibility.
_DELETE_BUTTON_LABEL = "Delete Line Item"
_DELETE_BUTTON_SECTION = "Campaigns Sent For Activation"
_DELETE_BUTTON_FIELD = "Campaign Status"
_DELETE_BUTTON_VALUE = "Pre-Briefing"


async def _open_delete_button_settings(page: Page, timeout_ms: int) -> bool:
    """Open the inspected record-details page and select Delete Line Item."""
    await page.locator('header[data-testid="appTopbar"]').wait_for(
        timeout=timeout_ms
    )
    section_heading = page.get_by_role(
        "heading", name=_DELETE_BUTTON_SECTION, exact=True
    )
    await section_heading.scroll_into_view_if_needed(timeout=timeout_ms)
    section_selector = section_heading.locator(
        'xpath=ancestor::*[@role="button" and @aria-label="Select element"][1]'
    )
    await (await _require_one(
        page,
        section_selector,
        f"{_DELETE_BUTTON_SECTION!r} section selection control",
        timeout_ms,
    )).click()

    configure = await _require_one(
        page,
        page.get_by_role(
            "button", name="Configure: Record detail pages", exact=True
        ),
        f"{_DELETE_BUTTON_SECTION!r} Record detail pages configuration button",
        timeout_ms,
    )
    await configure.click()
    detail_pages = page.get_by_test_id("popover:Record detail pages")
    customize = detail_pages.get_by_role(
        "button", name="Customize Flow Core Planning page", exact=True
    )
    await (await _require_one(
        page,
        customize,
        f"{_DELETE_BUTTON_SECTION!r} detail-page Customize button",
        timeout_ms,
    )).click()

    delete_text = page.get_by_text(_DELETE_BUTTON_LABEL, exact=True)
    delete_button = await _require_one(
        page,
        delete_text.locator(
            'xpath=ancestor::*[@role="button" and @aria-label="Select element"][1]'
        ),
        f"{_DELETE_BUTTON_LABEL!r} button",
        timeout_ms,
        exact_text=_DELETE_BUTTON_LABEL,
    )
    await delete_button.click()
    await _require_one(
        page,
        page.get_by_test_id("page-creator-sidebar").get_by_role(
            "button", name="open Visibility popover", exact=True
        ),
        f"{_DELETE_BUTTON_LABEL!r} Visibility settings",
        timeout_ms,
    )
    return True


async def _open_delete_visibility(page: Page, timeout_ms: int) -> Locator:
    sidebar = page.get_by_test_id("page-creator-sidebar")
    opener = await _require_one(
        page,
        sidebar.get_by_role(
            "button", name="open Visibility popover", exact=True
        ),
        f"{_DELETE_BUTTON_LABEL!r} Visibility settings",
        timeout_ms,
    )
    await opener.click()
    dialog = page.get_by_test_id("popover:Filter")
    await _require_one(page, dialog, "Visibility Filter dialog", timeout_ms)
    return dialog


async def _delete_visibility_is_correct(
    page: Page, timeout_ms: int, *, close: bool = True
) -> bool:
    """Read the complete one-condition state without changing it."""
    dialog = await _open_delete_visibility(page, timeout_ms)
    prefixes = dialog.get_by_test_id("filter-prefix-label")
    correct = False
    if await prefixes.count() == 1:
        row = prefixes.locator("xpath=ancestor::*[@aria-label][1]")
        normalized = " ".join((await row.inner_text()).split())
        correct = re.fullmatch(
            rf"(?:Where )?{re.escape(_DELETE_BUTTON_FIELD)} is "
            rf"{re.escape(_DELETE_BUTTON_VALUE)}",
            normalized,
        ) is not None
    if close:
        await page.keyboard.press("Escape")
    return correct


async def _ensure_delete_visibility(page: Page, timeout_ms: int) -> bool:
    """Set the sole visibility rule, or skip when its complete state is correct."""
    dialog = await _open_delete_visibility(page, timeout_ms)
    prefixes = dialog.get_by_test_id("filter-prefix-label")
    if await prefixes.count() == 0:
        add_condition = await _require_one(
            page,
            dialog.get_by_role("button", name="Add condition", exact=True),
            "Add visibility condition button",
            timeout_ms,
        )
        await add_condition.click()
    if await prefixes.count() != 1:
        raise _CandidateError(
            "Expected exactly one Delete Line Item visibility condition."
        )
    row = prefixes.locator("xpath=ancestor::*[@aria-label][1]")
    row_text = " ".join((await row.inner_text()).split())
    if re.fullmatch(
        rf"(?:Where )?{re.escape(_DELETE_BUTTON_FIELD)} is "
        rf"{re.escape(_DELETE_BUTTON_VALUE)}",
        row_text,
    ):
        await page.keyboard.press("Escape")
        return False

    field = row.locator(
        '[data-testid="autocomplete-button"][role="button"]'
    ).filter(
        has_not_text=re.compile(
            r"^(?:contains|does not contain|is|is not|is empty|is not empty)$",
            re.IGNORECASE,
        )
    )
    await (await _require_one(
        page, field, "visibility condition field", timeout_ms
    )).click()
    field_search = await _require_one(
        page,
        page.get_by_role("combobox", name="Find a field", exact=True),
        "visibility field search",
        timeout_ms,
    )
    await field_search.fill(_DELETE_BUTTON_FIELD)
    await (await _require_one(
        page,
        page.get_by_role("option").filter(
            has_text=re.compile(rf"^{re.escape(_DELETE_BUTTON_FIELD)}$")
        ),
        f"{_DELETE_BUTTON_FIELD!r} field option",
        timeout_ms,
        exact_text=_DELETE_BUTTON_FIELD,
    )).click()

    value = await _require_one(
        page,
        dialog.get_by_role("combobox", name=_DELETE_BUTTON_FIELD, exact=True),
        f"{_DELETE_BUTTON_FIELD!r} value selector",
        timeout_ms,
    )
    await value.click()
    await (await _require_one(
        page,
        page.get_by_role("option").filter(
            has_text=re.compile(rf"^{re.escape(_DELETE_BUTTON_VALUE)}$")
        ),
        f"{_DELETE_BUTTON_VALUE!r} option",
        timeout_ms,
        exact_text=_DELETE_BUTTON_VALUE,
    )).click()
    await page.keyboard.press("Escape")
    return True


# 10. Hide Delete button: apply its condition and verify it survives reload.
@_register(writes_data=True)
async def hide_delete_button(page: Page, settings: Settings) -> ActionReport:
    """Show Delete Line Item only when Campaign Status is Pre-Briefing."""
    action_name = "hide_delete_button"
    report = _new_report(action_name, page)
    timeout_ms = settings.browser_timeout_ms

    try:
        await _open_delete_button_settings(page, timeout_ms)
        changed = await _ensure_delete_visibility(page, timeout_ms)
        if changed:
            await _wait_for_interface_save(page, timeout_ms)
        correct = await _delete_visibility_is_correct(page, timeout_ms)
        if not correct:
            raise _CandidateError(
                "Delete Line Item visibility did not match Campaign Status is Pre-Briefing."
            )
        report.results.append(CandidateResult(
            candidate="Delete Line Item visibility",
            outcome=Outcome.SUCCESS if changed else Outcome.SKIPPED,
            message=(
                "Applied and validated Campaign Status is Pre-Briefing."
                if changed else
                "Campaign Status is Pre-Briefing is already configured."
            ),
        ))

        await page.reload(wait_until="domcontentloaded")
        await _open_delete_button_settings(page, timeout_ms)
        if not await _delete_visibility_is_correct(page, timeout_ms):
            raise _CandidateError(
                "Delete Line Item visibility did not persist after reload."
            )
        report.results.append(CandidateResult(
            candidate="Delete Line Item reload persistence",
            outcome=Outcome.SUCCESS,
            message="Verified the visibility condition after reload.",
        ))
    except (_CandidateError, PlaywrightError) as exc:
        report.results.append(CandidateResult(
            candidate="Delete Line Item visibility workflow",
            outcome=Outcome.FAILED,
            message=str(exc),
            details={"stage_url": page.url},
        ))

    return report.finish(page)


# 11. Change Request filter: add the two QA Review status conditions and verify.
_CHANGE_REQUEST_FIELD = "Status (from QA Reviews)"
_CHANGE_REQUEST_CONDITIONS = (
    (1, "is exactly", "QA Complete"),
    (2, "is empty", None),
)
_CHANGE_REQUEST_WARNING = "This view is used by automations"


async def _open_change_request_filters(page: Page, timeout_ms: int) -> None:
    opener = await _require_one(
        page,
        page.get_by_role("button", name="Filter rows", exact=True),
        "Filter rows button",
        timeout_ms,
    )
    await opener.click()
    for group_number, _, _ in _CHANGE_REQUEST_CONDITIONS:
        await _change_request_add_button(page, group_number, timeout_ms)


async def _change_request_add_button(
    page: Page, group_number: int, timeout_ms: int
) -> Locator:
    button = page.get_by_role(
        "button",
        name=f"add conditions and subgroups to condition group {group_number}",
        exact=True,
    )
    await _require_one(
        page, button, f"condition group {group_number} add button", timeout_ms
    )
    return button


def _change_request_pattern(
    group_number: int, operator: str, value: str | None
) -> re.Pattern[str]:
    tail = operator if value is None else f"{operator} {value}"
    return re.compile(
        rf"^(?:Where|and|or) {group_number}\.\d+: "
        rf"{re.escape(_CHANGE_REQUEST_FIELD)}\s+{re.escape(tail)}$",
        re.IGNORECASE,
    )


async def _change_request_condition_exists(
    page: Page, group_number: int, operator: str, value: str | None
) -> bool:
    pattern = _change_request_pattern(group_number, operator, value)
    labels = await page.locator("[aria-label]").evaluate_all(
        "elements => elements.map(element => element.getAttribute('aria-label'))"
    )
    return any(
        label and pattern.fullmatch(" ".join(label.split())) for label in labels
    )


def _blank_change_request_rows(page: Page, group_number: int) -> Locator:
    return page.locator(
        f'div[aria-label^="and {group_number}."]'
        '[aria-label$=": Name contains an unset filter value"]'
    )


async def _configure_change_request_condition(
    page: Page,
    group_number: int,
    operator: str,
    value: str | None,
    timeout_ms: int,
) -> None:
    blank_row = _blank_change_request_rows(page, group_number)
    blank_count = await blank_row.count()
    if blank_count > 1:
        raise _CandidateError(
            f"Found {blank_count} incomplete Name conditions in group "
            f"{group_number}; refusing to choose by position."
        )
    if blank_count == 0:
        add_button = await _change_request_add_button(
            page, group_number, timeout_ms
        )
        await add_button.click()
        menu_item = page.get_by_role("menu").get_by_role("menuitem").filter(
            has_text=re.compile(r"^Add condition$")
        )
        await (await _require_one(
            page,
            menu_item,
            "Add condition menu item",
            timeout_ms,
            exact_text="Add condition",
        )).click()

    blank_handle = await _require_one(
        page,
        blank_row,
        f"new incomplete Name condition in group {group_number}",
        timeout_ms,
    )
    blank_label = " ".join(
        (await blank_handle.get_attribute("aria-label") or "").split()
    )
    item_match = re.fullmatch(
        rf"and ({group_number}\.\d+): Name contains an unset filter value",
        blank_label,
    )
    if item_match is None:
        raise _CandidateError(
            f"The new condition had an unexpected label: {blank_label!r}."
        )

    row = page.locator(f'div[aria-label^="and {item_match.group(1)}:"]')
    field_button = row.locator(
        '[data-testid="autocomplete-button"][role="button"]'
    ).filter(has_text=re.compile(r"^Name$"))
    await (await _require_one(
        page,
        field_button,
        f"new condition field in group {group_number}",
        timeout_ms,
        exact_text="Name",
    )).click()

    field_search = await _require_one(
        page,
        page.get_by_role("combobox", name="Find a field", exact=True),
        "filter field search",
        timeout_ms,
    )
    await field_search.fill(_CHANGE_REQUEST_FIELD)
    await (await _require_one(
        page,
        page.get_by_role("option").filter(
            has_text=re.compile(rf"^{re.escape(_CHANGE_REQUEST_FIELD)}$")
        ),
        f"{_CHANGE_REQUEST_FIELD!r} field option",
        timeout_ms,
        exact_text=_CHANGE_REQUEST_FIELD,
    )).click()

    default_operator = "has any of"
    operator_button = row.locator(
        '[data-testid="autocomplete-button"][role="button"]'
    ).filter(has_text=re.compile(rf"^{re.escape(default_operator)}$"))
    await (await _require_one(
        page,
        operator_button,
        f"new condition operator in group {group_number}",
        timeout_ms,
        exact_text=default_operator,
    )).click()
    option_text = f"{operator}..." if operator == "is exactly" else operator
    await (await _require_one(
        page,
        page.get_by_role("option").filter(
            has_text=re.compile(rf"^{re.escape(option_text)}$")
        ),
        f"{operator!r} operator option",
        timeout_ms,
        exact_text=option_text,
    )).click()

    if value is not None:
        value_button = row.locator(
            '[data-testid="autocomplete-button"][role="button"]'
        ).filter(has_text=re.compile(r"^Select an option$"))
        await (await _require_one(
            page,
            value_button,
            f"new condition value in group {group_number}",
            timeout_ms,
            exact_text="Select an option",
        )).click()
        await (await _require_one(
            page,
            page.get_by_role("option").filter(
                has_text=re.compile(rf"^{re.escape(value)}$")
            ),
            f"{value!r} value option",
            timeout_ms,
            exact_text=value,
        )).click()

    if not await _change_request_condition_exists(
        page, group_number, operator, value
    ):
        expected = f"{_CHANGE_REQUEST_FIELD} {operator}"
        if value:
            expected += f" {value}"
        raise _CandidateError(
            f"The new condition in group {group_number} did not become {expected!r}."
        )


async def _apply_change_request_filters(page: Page, timeout_ms: int) -> None:
    apply_button = await _require_one(
        page,
        page.get_by_role("button", name="Apply", exact=True),
        "Apply filters button",
        timeout_ms,
        exact_text="Apply",
    )
    await apply_button.click()
    warning = page.get_by_role(
        "heading", name=_CHANGE_REQUEST_WARNING, exact=True
    )
    try:
        await warning.wait_for(state="visible", timeout=min(timeout_ms, 4_000))
    except PlaywrightError:
        await page.wait_for_timeout(2_500)
        return

    dialog = warning.locator('xpath=ancestor::*[@role="dialog"][1]')
    continue_button = dialog.get_by_role("button", name="Continue", exact=True)
    if await continue_button.count() == 0:
        continue_button = page.get_by_role(
            "button", name="Continue", exact=True
        )
    await (await _require_one(
        page,
        continue_button,
        "automation warning Continue button",
        timeout_ms,
        exact_text="Continue",
    )).click()
    await page.wait_for_timeout(2_500)


@_register(writes_data=True)
async def change_request_add_filter(
    page: Page, settings: Settings
) -> ActionReport:
    """Add the two QA Review status filters to their existing condition groups."""
    action_name = "change_request_add_filter"
    report = _new_report(action_name, page)
    timeout_ms = settings.browser_timeout_ms
    changed = False

    try:
        await page.locator('header[data-testid="appTopbar"]').wait_for(
            timeout=timeout_ms
        )
        await _open_change_request_filters(page, timeout_ms)

        for group_number, operator, value in _CHANGE_REQUEST_CONDITIONS:
            label = f"{_CHANGE_REQUEST_FIELD} {operator}"
            if value:
                label += f" {value}"
            if await _change_request_condition_exists(
                page, group_number, operator, value
            ):
                report.results.append(CandidateResult(
                    candidate=f"Condition group {group_number}: {label}",
                    outcome=Outcome.SKIPPED,
                    message="The exact condition is already configured.",
                ))
                continue

            await _configure_change_request_condition(
                page, group_number, operator, value, timeout_ms
            )
            changed = True
            report.results.append(CandidateResult(
                candidate=f"Condition group {group_number}: {label}",
                outcome=Outcome.SUCCESS,
                message="Added the condition from the saved click inspection.",
            ))

        if changed:
            await _apply_change_request_filters(page, timeout_ms)
        else:
            await page.keyboard.press("Escape")

        await page.reload(wait_until="domcontentloaded")
        await page.locator('header[data-testid="appTopbar"]').wait_for(
            timeout=timeout_ms
        )
        await _open_change_request_filters(page, timeout_ms)
        for group_number, operator, value in _CHANGE_REQUEST_CONDITIONS:
            if not await _change_request_condition_exists(
                page, group_number, operator, value
            ):
                raise _CandidateError(
                    f"Condition group {group_number} did not retain "
                    f"{_CHANGE_REQUEST_FIELD} {operator}"
                    f"{f' {value}' if value else ''} after reload."
                )
        await page.keyboard.press("Escape")
        report.results.append(CandidateResult(
            candidate="Change Request filter reload persistence",
            outcome=Outcome.SUCCESS,
            message="Verified both conditions in their numbered groups after reload.",
        ))
    except (_CandidateError, PlaywrightError) as exc:
        report.results.append(CandidateResult(
            candidate="Change Request filter workflow",
            outcome=Outcome.FAILED,
            message=str(exc),
            details={"stage_url": page.url},
        ))

    return report.finish(page)


