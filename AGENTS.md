# Building browser automation

## Inspection workflow

Use the normal-click DOM recorder before designing a new Airtable workflow:

```powershell
python main.py inspect --url "https://airtable.com/app.../..." --label "task-name"
```

The user performs the complete workflow normally and then closes Chrome. Read the
resulting JSON in `inspections/` in click order. Use its actionable element,
region, ancestors, pointer stack, state, frame, and post-click overlays to build
semantic Playwright locators. Screenshots may provide visual context, but are not
the selector source. There is no ALT-click or snapshot inspection mode.

Recordings describe clicks; production workflows must still account for current
state. Prefer roles, accessible names, stable IDs, `data-testid`, exact text, and
scoped containing regions. Avoid coordinates, generated CSS classes, positional
selection, and unconditional `.first()`, `.last()`, or `.nth()`.

## Code organization

- Keep only new public registered workflows in root `functions.py`.
- Put helpers for new workflows in `backend/helpers.py`; do not add helpers to
  `functions.py`.
- Preserve old workflows in `backend/old_core_global_changes.py`. Do not modify
  them unless the user explicitly requests legacy maintenance.
- Keep shared browser, configuration, recorder, runner, reporting, and registry
  code in `backend/`.
- Keep CLI parsing and dispatch in root `main.py`.
- Keep the simple Inspect/Run desktop UI in root `launcher.pyw`.
- Keep the launcher Run inventory limited to `apps.json` and `15-PH-apps.json`;
  show app names with URLs and default multi-selection to all entries.
- Register workflows with `backend.airtable_actions._register`. The launcher and
  normal `run` command discover only `functions.py`; `run-old` is terminal-only.
- Keep new code short. Add docstrings and comments only for important phases,
  state decisions, or non-obvious safety behavior.
- Keep `functions.py` compact and use comments only for important decisions.

## Workflow safety and validation

### Inspection fidelity and testing

- Follow the inspected click sequence closely, including intermediate container
  selections that make later controls available; do not skip recorded clicks
  merely because a shorter route appears possible.
- Treat nested controls with the same accessible name as distinct targets. Trace
  the actionable element, its clickable ancestors, pointer stack, and region to
  identify whether the recording clicked a small inner control or a large outer
  wrapper. When this remains ambiguous, request the relevant HTML or screenshot
  before implementing; screenshots are context, not selector sources.
- Expect selection to change the DOM (for example, a heading becoming a
  textarea). Validate the post-click state using the control that exists after
  the click instead of searching for the element that was replaced.
- Preserve live Playwright locators across Airtable navigation and loading
  transitions. Do not retain an ElementHandle for a later click when Airtable may
  rerender the sidebar or page; validate the locator, then click the locator so
  Playwright can resolve the current node and retry through transient overlays.
- Do not infer successful duplication from simultaneous visibility of both
  copies; off-screen and virtualized sections may not render together. Follow the
  selected duplicate into its next inspected state and verify the final unique
  title or setting instead.
- Recover partial write states only when they are uniquely and explicitly
  identifiable. Otherwise reject ambiguity rather than creating another copy or
  repeating a destructive action.
- Use the exact final text and exact fields demonstrated by the inspection. Do
  not add an extra rename or setting change merely because an earlier recording
  appeared to imply one.
- Test and improve new workflows using the smallest explicit target that covers
  the changed path. Keep test cycles focused and review the resulting report and
  failure screenshot before changing selectors.
- After navigation, save, or reload, wait for a specific expected semantic state
  rather than treating `domcontentloaded` or the first visible container as
  completion. Reload persistence checks must allow Airtable's canvas to rerender.

Follow **find -> validate -> act -> verify -> report**. Discover all plausible
candidates, reject ambiguity, skip already-correct state, use normal Playwright
interactions, and verify the final state. Account for collapsed sections,
scrolling, virtualized content, frames, duplicate names, delayed rendering, and
menus or dialogs that may already be open or closed.

For multi-item sections, use `_run_validated_section`: apply the section, validate
the complete expected state, retry only unresolved items up to the configured
limit, and stop when the section remains incomplete. Never retry a toggle blindly.
Verify persistence after reload when appropriate. Report every target and treat
unprocessed or unverified targets as failures.

Before a live write trial, restate the intended operation and use one explicitly
authorized URL or app. Review the Excel report and failure screenshot before any
broader run. Local browser fixtures and read-only discovery do not count as live
write validation.

## Runtime conventions

- `apps.json` is the active app inventory unless `.env` sets `APPS_FILE`.
- Use `backend.config.lebanon_now()` for timestamps.
- Inspection and report names use
  `<descriptive-name>  -  MM-DD__HH.MM`, adding a numbered suffix on collision.
- Failure screenshots remain in `backend/screenshots/` with their existing
  timestamp format.
- Runtime artifacts, `.env`, browser profiles, caches, and virtual environments
  stay outside source control and must not be copied or shared.
- Keep README sections numbered and setup dependencies inline; do not add a
  `requirements.txt` unless requested.
- Do not create VS Code launch configurations unless requested.
