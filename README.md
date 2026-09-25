# Airtable Interface Automation

A production-oriented Python and Playwright foundation for inspecting Airtable
Interface Designer and applying safe, repeatable actions across multiple apps.
Authentication is performed manually in Google Chrome and retained in one local
persistent profile. The automation does not automate or bypass sign-in.

## 1. Architecture

```text
main.py                       # CLI commands and entry point
launcher.pyw                  # Double-clickable Inspect / Run window
backend/
    airtable_actions.py       # Registry, shared result types, element validation
    browser.py                # Browser lifecycle
    config.py                 # Settings and apps.json loading
    inspector.py              # Automatic and ALT-click inspection
    runner.py                 # Execution across apps
    reporting.py              # Excel reports
    screenshots/              # Failure screenshots for diagnosis
core_global_changes.py        # Registered Airtable business workflows
apps.json                     # App names and URLs
AGENTS.md                     # Instructions for future development chats
```

Batch runs launch one persistent Chrome context. Each active app gets a separate
Playwright page, and a bounded worker pool controls concurrency. When one app job
finishes, that worker starts the next queued app. All requested actions for the
same app execute serially, so write actions can never modify one app at the same
time. Failures are isolated to their app/action and do not stop other jobs.

Each Airtable action still accepts exactly one page and knows nothing about apps,
queues, concurrency, Excel, or browser ownership.

## 2. Setup

Python 3.11 or newer and installed Google Chrome are recommended:

```powershell
python -m pip install "playwright>=1.45,<2.0" "openpyxl>=3.1,<4.0" tzdata
```

A virtual environment is optional but recommended for package isolation.

The browser uses Playwright's `channel="chrome"`; no separate Chromium download is
required.

## 3. Configure apps

Add every app to `apps.json` with a unique name and URL:

```json
[
  {
    "name": "Market A",
    "url": "https://airtable.com/app.../..."
  },
  {
    "name": "Market B",
    "url": "https://airtable.com/app.../..."
  }
]
```

Only app inventory belongs in this file. Runtime controls remain in `.env`:

```text
APPS_FILE=apps.json
CONCURRENCY=3
CHROME_PROFILE_DIR=chrome-profile
REPORTS_DIR=reports
SCREENSHOTS_DIR=backend/screenshots
BROWSER_TIMEOUT_MS=15000
NAVIGATION_TIMEOUT_MS=45000
HEADLESS=false
SLOW_MO_MS=0
```

With `CONCURRENCY=3`, three apps run simultaneously in three pages inside the
same authenticated Chrome context. Do not start a second program instance against
the same `chrome-profile` while a run is active.

## 4. Inspector

The agreed workflow is:

1. Inspect the example URL automatically, or use the user's ALT-click captures.
2. Build the workflow safely in root `core_global_changes.py`.
3. Test it, review failures, fix the implementation, and verify again.
4. Run one authorized app, review its results, then run the remaining apps when authorized.


Capture accessibility structure, useful attributes, and ancestor context in every
frame automatically, without ALT-clicks:

```powershell
python main.py inspect --snapshot --url "https://airtable.com/appq32EZOToUF8Bz7/wflbh7VR2rAueZExL"
```

New inspections are named `<app-name-or-base-id>__<task-label>__<clicks-or-structure>  -  MM-DD__HH.MM.json`.
For example, `Market-A__smart-import-setup__structure  -  09-18__11.30.json`.
Use `--label smart-import-setup` with either inspection mode to describe the task.
Existing inspection files retain their names. The JSON snapshot is saved under `inspections/`. This requires an authenticated
automation Chrome profile. ALT-click remains available for focused inspection.

Inspect the first configured app:

```powershell
python main.py inspect
```

Inspect a named app or an explicit URL:

```powershell
python main.py inspect --app "Market A"
python main.py inspect --url "https://airtable.com/app.../..."
```

Sign in manually when prompted. Hold **ALT** and click relevant elements. Concise
JSON records are written under `inspections/`; press `Ctrl+C` when finished. An
inspection `selector_hint` is evidence for locator design, not a uniqueness claim.

## 5. Run actions

### 5.1. Simple launcher window

Double-click `launcher.pyw` in File Explorer to open the launcher. This uses your
Windows Python installation with the dependencies from section 2 installed.

1. Choose **Inspect** or **Run**.
2. For Inspect, enter the Airtable URL and click **Inspect**. ALT-click elements
   in Chrome, then close the automation browser when finished.
3. For Run, choose the function and select **One URL** or **All apps in apps.json**.
4. Enter a URL when using One URL, then click **Run**. A URL trial does not require
   adding the URL to `apps.json`.
5. Read progress and the final result in the window. Runs produce the normal Excel
   report, and its location appears in the output. Newly registered functions
   appear automatically when the launcher is reopened.

Use **Open inspection** or **Open report** beneath the activity output to open
the file in its default application. Buttons activate when the current operation's
file exists. Inspection files become available after the first ALT-click capture.

The window stays responsive while Chrome runs. If `.pyw` files do not open with
Python on your computer, use **Open with ? Python**, or run `python launcher.pyw`
once from the project folder. The launcher uses Tkinter, included in the standard
Windows Python installation.

### 5.2. Saved commands file

Open `commands.txt`, edit an app name or URL if needed, and select one command.
Press `Ctrl+Shift+P` and choose **Terminal: Run Selected Text in Active Terminal**
to send and run that command in your terminal. The terminal must be in the project
folder. Commands targeting ALL apps are labeled explicitly in the file.

You can assign a keyboard shortcut to that command in VS Code's Keyboard
Shortcuts settings for faster use. See the [VS Code terminal documentation](https://code.visualstudio.com/docs/terminal/basics).

### 5.3. Terminal commands

List configured apps and registered actions:

```powershell
python main.py list-apps
python main.py list-actions
```

Run one action across every app:

```powershell
python main.py run remove_publicis_media_team_signature
```

Override concurrency or select named apps:

```powershell
python main.py run remove_publicis_media_team_signature --concurrency 2
python main.py run remove_publicis_media_team_signature --app "Market A" --app "Market B"
python main.py run smart_import_core --app "FlowAi | Master | Walmart"
python main.py run hide_suggested_metric --url "https://airtable.com/app.../.../edit"
python main.py run change_request_add_filter --url "https://airtable.com/app.../tbl.../viw..."
```

`smart_import_core` performs the captured part-1 setup sequence. Its ordered
field and table targets are kept in `_SMART_IMPORT_EXTRA_FIELDS` and
`_SMART_IMPORT_LINKED_TABLES`. After adding the fields, the action opens the
Interface Designer Table popover, ensures all six linked tables are visible,
and enables per-table inline record editing for the inspected table set. Each
section applies all targets before validating the complete final state, with up
to two retries of only missing or incorrect items. An incomplete section reports
the failed items and stops the setup sequence. Interface saves are awaited once
per section attempt.

`hide_suggested_metric` opens Flow Core Planning and its configured Brief Details
page, then hides the exact `Suggested Metric` field in both **Add/Edit Media
Plans** and **Review Media Plans**. It skips a section when that exact field is
already hidden, validates both sections as a complete set, reloads the detail
page, and verifies that both hidden states persisted.

`change_request_add_filter` updates the current Change Requests view filter. It
adds `Status (from QA Reviews) is exactly QA Complete` to condition group 1 and
`Status (from QA Reviews) is empty` to condition group 2, then verifies both
conditions after reloading the view.

The runner already accepts multiple actions. For each app, those actions execute
one at a time in the order provided:

```powershell
python main.py run smart_import_core remove_publicis_media_team_signature
```

Each run produces exactly one `.xlsx` workbook under `reports/`.
New reports begin with the function name followed by month-day and hour.minute, for example
`smart_import_core  -  09-18__11.30.xlsx`.
The year and seconds are omitted. All timestamps use Lebanon time (`Asia/Beirut`), including daylight-saving changes. A numbered suffix is added
when a file with the same name already exists, so repeat runs do not overwrite it. Existing files retain their names. It contains App,
Action, Result, Message, Duration, and Failure Information columns. No JSON or CSV
report is generated. Detailed exceptions appear in the terminal only; no log files are saved.
Screenshots are saved under `backend/screenshots/` for failed app/action executions.

## 6. Adding an action

Add public async workflows and their private helpers to root
`core_global_changes.py`. Keep business logic out
of `backend/airtable_actions.py`, which provides the registry and shared types.

```python
from backend.airtable_actions import ActionReport, _register
from backend.config import Settings
from playwright.async_api import Page

@_register(writes_data=True)
async def descriptive_action(page: Page, settings: Settings) -> ActionReport:
    ...
```

Use clear numbered comments for major sections and each public workflow.
Comment only significant phases or decisions; avoid comments for every small step.

Existing CLI action names and commands remain unchanged.

For multi-section setup actions, use `_run_validated_section` for every major
section, including navigation that opens a configuration panel. Apply the whole
section before checking all expected final states, retry only unresolved targets
(up to two retries), and stop if the helper returns False. The helper records an
explicit section validation result and unresolved items. Click completion alone
must not count as section success. Excel shows the overall run result and places
failed items and their explanations first; any failed action makes the run FAILED.

Keep helpers private with a leading underscore. Actions should follow **find ->
validate -> act -> verify -> report**, inspect every plausible candidate, safely
skip already-correct state, and return a `CandidateResult` for each intended
candidate. Avoid coordinates, generated CSS classes, positional selection, and
unconditional `.first()`, `.last()`, or `.nth()` calls.

## 7. Runtime data and safety

The persistent profile, `.env`, screenshots, inspections, reports, virtual
environment, and caches are gitignored. The Chrome profile contains sensitive
session data and must never be copied, shared, or committed. Screenshots and
inspection records may also contain sensitive Airtable content.

