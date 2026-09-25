# Airtable Interface Automation

Python and Playwright automation for inspecting Airtable pages and applying
verified workflows through an authenticated Chrome profile.

## 1. Project structure

```text
main.py                       # CLI entry point
launcher.pyw                  # Double-clickable Inspect/Run window
functions.py                  # New launcher-visible workflows only
apps.json                     # Active app inventory
15-PH-apps.json               # Alternate 15-app inventory
backend/
    airtable_actions.py       # Registry, reports, and element validation
    browser.py                # Sync and async Chrome sessions
    config.py                 # Environment, app, and timestamp settings
    helpers.py                # Reusable helpers for new workflows
    old_core_global_changes.py # Preserved legacy workflows
    recorder.py               # Normal-click DOM recorder
    reporting.py              # Excel report generation
    runner.py                 # Controlled concurrent execution
```

Runtime folders such as `chrome-profile/`, `inspections/`, `reports/`, caches,
and failure screenshots are not source code.

## 2. Setup

Python 3.11 or newer and Google Chrome are recommended:

```powershell
python -m pip install "playwright>=1.45,<2.0" "openpyxl>=3.1,<4.0" tzdata
```

Configuration belongs in `.env`:

```text
APPS_FILE=apps.json
CONCURRENCY=3
CHROME_PROFILE_DIR=chrome-profile
INSPECTIONS_DIR=inspections
REPORTS_DIR=reports
SCREENSHOTS_DIR=backend/screenshots
BROWSER_TIMEOUT_MS=15000
NAVIGATION_TIMEOUT_MS=45000
HEADLESS=false
SLOW_MO_MS=0
```

The persistent Chrome profile keeps the manual Airtable login. Do not run two
project processes against that profile simultaneously.

## 3. App inventory

`apps.json` contains the apps used by named and all-app runs:

```json
[
  {
    "name": "Market A",
    "url": "https://airtable.com/app..."
  }
]
```

Names and URLs must be unique. The launcher can switch between `apps.json` and
`15-PH-apps.json`. For terminal commands, select the alternate inventory by
setting `APPS_FILE=15-PH-apps.json` in `.env` or for that process.

## 4. Record a workflow

Start the normal-click DOM recorder:

```powershell
python main.py inspect --url "https://airtable.com/app.../..." --label "task-name"
```

You can also inspect the first configured app or a named app:

```powershell
python main.py inspect
python main.py inspect --app "Market A" --label "task-name"
```

Chrome opens with the saved profile. Perform the complete workflow normally,
wait briefly after the final click, and close Chrome. The JSON file under
`inspections/` records, for each click:

- the event target and nearest actionable control;
- the containing region and ancestor path;
- pointer coordinates and the elements under the pointer;
- attributes, text, role, and relevant state;
- visible menus, listboxes, or dialogs after the click;
- the page and frame URL.

The recording is inspection evidence, not an automatic replay script. To create
a production workflow, provide the recording plus the function name, required
inputs, and expected final state. The implementation converts the evidence into
semantic locators and adds state checks, verification, and reporting.

There is no ALT-click or snapshot inspection mode.

## 5. Run workflows

List available apps, new functions, and preserved old functions:

```powershell
python main.py list-apps
python main.py list-actions
python main.py list-old-actions
```

Run a new function on one explicit URL, selected configured apps, or every app:

```powershell
python main.py run new_function --url "https://airtable.com/app.../..."
python main.py run new_function --app "Market A" --app "Market B"
python main.py run new_function
```

Multiple functions run serially for each app:

```powershell
python main.py run first_function second_function
```

Old functions are excluded from the launcher and the normal `run` command. Run
one manually from the terminal with the explicit legacy command:

```powershell
python main.py run-old hide_suggested_metric --url "https://airtable.com/app.../..."
```

Apps may run concurrently, controlled by `CONCURRENCY` or `--concurrency`. Each
app uses one page and never executes two write workflows simultaneously.

Every run creates one `.xlsx` report in `reports/`. Failed workflows include
diagnostic information and may save a screenshot under `backend/screenshots/`.

## 6. Preserved old workflows

These remain in `backend/old_core_global_changes.py` and are available only
through `list-old-actions` and `run-old`:

- `smart_import_core` configures Smart Import fields, linked tables, and inline
  record editing.
- `remove_publicis_media_team_signature` removes the exact final-line signature
  from the two configured approval-email actions.
- `hide_suggested_metric` hides `Suggested Metric` in both configured Flow Core
  media-plan sections and verifies persistence after reload.
- `hide_delete_button` applies the inspected `Campaign Status is Pre-Briefing`
  visibility rule to `Delete Line Item` and verifies it after reload.
- `change_request_add_filter` adds the two inspected QA Review status conditions
  to their numbered groups and verifies them after reload.

## 7. Desktop launcher

Double-click `launcher.pyw`, or run:

```powershell
python launcher.pyw
```

Choose **Inspect** to record one URL. Choose **Run**, select `apps.json` or
`15-PH-apps.json`, then select the displayed app names and URLs. Every app is
selected by default; use Ctrl-click, **Select all**, or **Clear** to change the
selection. The launcher streams terminal output and enables **Open inspection**
or **Open report** when the corresponding file exists.

## 8. Adding a workflow

Add only the public registered function to root `functions.py`. Put reusable or
lengthy implementation details in `backend/helpers.py`:

```python
from backend.airtable_actions import ActionReport, _register
from backend.config import Settings
from playwright.async_api import Page


@_register(writes_data=True)
async def descriptive_action(page: Page, settings: Settings) -> ActionReport:
    ...
```

Use **find -> validate -> act -> verify -> report**. Reject ambiguous targets,
skip already-correct state, and verify persistence when appropriate. The
launcher automatically displays only registered functions from `functions.py`.

## 9. Data safety

The Chrome profile contains authenticated session data. Inspection JSON,
screenshots, and reports may contain Airtable content. Do not copy or share these
artifacts. Workflows should be trialed on one authorized URL before broader runs.
