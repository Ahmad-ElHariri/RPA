# Building browser automation

For each new task, inspect the live page automatically before designing selectors.
Use `python main.py inspect --snapshot --url <url>` to collect accessibility and
DOM structure, including frames. Prefer documented APIs when they provide the
needed operation with existing credentials; otherwise use Playwright semantics.
Use ALT-click inspection only to resolve details missing from automatic evidence.

Identify the containing region, repeated rows, stable IDs, selected state, and
save/checked state. Account for collapsed sections, search filters, scrolling,
virtualized content, frames, duplicate names, and delayed rendering. Never infer
live selectors solely from a screenshot or choose an ambiguous match by position.

Bulk actions should discover targets once, skip correct state, act through normal
Playwright clicks, verify state, and report every target. Process a shared detail
panel serially. Verify persistence after reload when appropriate. Do not retry a
toggle blindly. Keep task-specific decisions in helpers and expose reusable
actions through the existing action registry and Excel reporting runner.

Keep business workflows and their task-specific helpers in root
`core_global_changes.py`. Shared browser, configuration, inspection, runner,
reporting, and action registry code lives in `backend/`. Keep command parsing
and the CLI entry point together in root `main.py`. Register functions using
`backend.airtable_actions._register`. Do not put business workflows in the
registry module. Keep new workflow and inspector code as short as practical.

Use `inspect --label <task-or-step>` for clear inspection filenames. Before a live
trial, restate the user's intended steps and inspect the example URL. Test the
function, run a single authorized app, review verification and reports, then run
the remaining apps when authorized. Review failure screenshots when available,
alongside DOM/accessibility evidence and errors; a screenshot alone cannot verify
saved state. Report unprocessed targets and incomplete verification as failures.

Validate new behavior with representative local browser fixtures and read-only
live discovery. Clearly distinguish fixture validation from live write validation.

Use clear numbered comments for major sections and public workflows in
`core_global_changes.py`. Add comments only for necessary phases or decisions.
Failure screenshots belong in `backend/screenshots/`. New inspection, report,
filenames use `<descriptive-name>  -  MM-DD__HH.MM` without the year.
Add a numbered suffix if needed to avoid overwriting existing files. Screenshot
filenames retain their existing timestamp format.
Keep README sections numbered and document dependencies directly in the setup
command; do not recreate requirements.txt.

The user prefers reusable terminal commands rather than Run and Debug launch
configurations. Do not recreate launch.json unless requested.

Keep reusable terminal commands in root `commands.txt`. The user selects a
command and sends it through Terminal: Run Selected Text in Active Terminal.
Maintain relevant entries when adding workflows. Clearly label ALL-app commands.

The primary user entry point is root `launcher.pyw`: a simple Inspect/Run window
with a function picker and One URL/All apps targets. Keep function discovery
automatic through the registry. The launcher dispatches to main.py and uses the
existing runner and reports. Do not run live workflows just to test the UI.

Use `backend.config.lebanon_now()` for all future timestamps (Asia/Beirut),
including inspections, report filenames and contents, screenshots, and logs.
Do not add UTC to filenames or report headings. Existing files remain unchanged.
