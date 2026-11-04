# Automation standards

Applies to everything under `scripts/automation/`. The repo-wide rules in the
root `CLAUDE.md` (US English, public repo, no bank names, ask before push/PR)
apply too.
Machine setup: [`machine-prep.md`](machine-prep.md).

## Building a new automation

The existing process folders here are the working examples; the steps to add a
package are in [`README.md`](README.md). Before writing code:

1. **Ask what isn't clear:** the site or source, what to capture, where it
   goes (CSV, Google Sheet, webhook message), how often, and whether it needs
   a sign-in. Keep real URLs and IDs out of the repo.
2. **Reuse before writing:** see "Shared code" below. Sign-in has its own
   caveat under "Browser rules".
3. **Learn the real page, don't guess.** With the maintainer's OK, load a
   public page once in the built-in browser and read its structure. Never sign
   in to anything yourself. If you can't load it, build from candidates plus a
   text fallback, add a `--dump` option, and ask the maintainer to run it and
   paste the output. Real labels often differ from what the request calls them.
4. **Write a synthetic fixture** that mirrors the real structure with made-up
   names and values, then the parser and its tests.

When you finish, tell the maintainer what you built, what you couldn't verify,
and what to check. Give the test steps as PowerShell commands for the robot
test workstation, in `powershell` code blocks, including anything only the
maintainer can do (secrets, Google or Home Assistant setup, config values).

## Naming and generic docs

- Process names, folders, packages, and commands never name a bank, even when
  the automation uses a bank's site. Use a neutral name (e.g. `account-recon`).
- This `CLAUDE.md`, `README.md`, `machine-prep.md`, and `common/` are generic:
  they use `<process>` and never name a specific process. Only a process's own
  folder and the workspace wiring (`pyproject.toml`, `uv.lock`) may name it.

## Machines

| Machine | Used for |
| --- | --- |
| Developer workstation | Where Claude Code runs: code, tests, commits, PRs. Runs on demand only, never scheduled |
| Robot test workstation | The maintainer tests branches end to end here. Read-only clone, no Claude Code |
| Unattended robot | Runs the scheduled jobs from `main`. Read-only clone, no Claude Code |

Live runs happen on the test workstation or robot, by the maintainer.

## Paths (Windows 11)

| Path | Purpose |
| --- | --- |
| `C:\src\homeLab` | Repo clone. Read-only on the robot (anonymous clone, push disabled) |
| `C:\src\homeLab\scripts\automation\<process>` | One folder per automation (code only) |
| `C:\automation\<process>\` | That automation's private, writable data, outside the repo |
| `C:\automation\<process>\config.toml` | Real config. Never read it |
| `C:\automation\<process>\data\` | Output data files |
| `C:\automation\<process>\runs\<yyyyMMdd_HHmmss>\` | Per-run log, report, summary, failure evidence, trace |
| `C:\automation\<process>\profiles\` | Persistent browser profiles |

- The robot runs as non-admin user `robot`, auto-signed-in and locked, from
  Task Scheduler. Nothing may wait for a human: no `input()`, no dialogs.
  The only exception is an explicit MFA wait with a timeout.
- Find the data folder only through `automation_common.config.process_dir(<process>)`:
  env var `AUTOMATION_DATA_ROOT` (default `C:\automation`) plus the process
  name. Tests use a temp folder.
- Never read anything under `C:\automation\`, including browser profiles and
  run folders. The maintainer copies what you need into `triage/`.

## Shared code

Use these from `automation_common` instead of writing your own:

| Module | Gives you |
| --- | --- |
| `run` | `Run`: run folder, log with secrets masked, summary, webhook notification, and exit code |
| `config` | `process_dir`, TOML loading, and validation helpers |
| `secrets` | Credential Manager entries and encrypted secret files |
| `browser` | `launch_edge`, `find_first`, `click_until`, `type_date`, tracing, and failure evidence |
| `sheets` | `open_worksheet`: read a Google Sheet tab and append rows |
| `notify` | The webhook POST (`Run` calls it for you) |

When copying a data file to a sheet, compare the two and append what's missing,
so the next run catches up after a failed write. A process with a `sheet.py`
shows how.

## Workspace and packaging

- Python 3.13, uv. One uv workspace rooted here with a single committed `uv.lock`.
  - `pyproject.toml`: workspace root, members, dev tools (pytest, ruff), and lint and test config.
  - `common/`: package `automation_common` (shared code).
  - `<process>/`: one package per automation, depending on `automation-common`
    as a workspace dependency, with a console script (e.g. `<process>-<what>`).
  - `tools/`: scripts for people (e.g. `env_check.py`).
- `uv sync` here installs everything. Add a dependency with `uv add` in the
  member folder, then commit the updated `uv.lock`.
- Config is TOML, every text value quoted. Each process commits a
  `config.example.toml` with placeholders. The loader validates the real file
  at start-up and fails with a clear message when it's missing, invalid, or
  still holds placeholders.

## Secrets

- Only through `automation_common.secrets`. Entry names `<process>/<name>`;
  shared ones `automation/<name>` (e.g. `automation/home-assistant-webhook`).
  - Short values (passwords, tokens, URLs): Windows Credential Manager of the
    `robot` user, via keyring (`get_secret`, `get_credential`).
  - Values over Credential Manager's 2,560-byte limit (e.g. the Google service
    account key `automation/google-service-account`): a DPAPI-encrypted file
    `%USERPROFILE%\.<prefix>\<name>.dpapi` (`read_secret_file`), created by the
    person with `tools/import_secret_file.py`.
- Never read, print, export, or store secret values, and never run `cmdkey`,
  `keyring`, or `tools/import_secret_file.py` yourself. Pass the run's
  `masker` so values are hidden in logs.
- Google Sheets: `automation_common.sheets.open_worksheet` (service account,
  Sheets scope only). Spreadsheet IDs are private config: add them to the
  masker and never put them in messages.

## Code rules

- Fetch, transform, and write are separate modules. Transforms/parsers are
  pure functions with unit tests.
- Use `automation_common.run.Run`: it creates the run folder and log, and
  always writes the summary (`summary.json`, `report.txt`) on exit. If the
  `automation/home-assistant-webhook` secret exists, it POSTs only
  `{"title": ..., "message": ...}` there (default: the process name and
  `PASS` or `FAIL`), which the Home Assistant automation shows as is;
  otherwise it only logs it. Send nothing else to the webhook: details stay
  in the run folder. Keep messages short: they are read on a watch. Pass
  `title=` or `message=` to `Run.start` to change them.
- Each stage, and each item within a stage (e.g. each fund), has its own
  try/except. Record one failure with `run.summary.record(...)`, then continue.
- Exit code: 0 all OK, 1 any item failed.
- `logging` only, never `print` (except tools for people). Never log
  secrets or private URLs.
- Type hints on public functions; no global state; small functions.

## Browser rules (proven on a real bank site; keep them)

- `automation_common.browser.launch_edge`: Playwright driving installed Edge
  (`channel="msedge"`), headed, `launch_persistent_context` with a profile
  under `profiles\`, `chromium_sandbox=True`, `accept_downloads=True`.
  Don't add flags that weaken security.
- Selectors are lists of candidates, most specific first; `find_first` uses
  the first VISIBLE match (pages hold hidden copies of controls).
- Never use fixed sleeps. Wait for elements, URLs, or `expect_download`.
- Date inputs: `type_date` (click, select all, type key by key, Tab, read back).
- Clicks: `click_until` (normal, then forced, then keyboard; verify after each).
- Never start a Playwright trace before any sign-in is complete.
- Sign-in and MFA stay in each process's own package, not `common/`: they're
  site-specific and the most likely part of a site to change. `automation_common`
  has no sign-in or MFA helper. The FakeBank flow in `tools/env_check.py` is a
  pattern to read, not a library to import. An MFA wait must time out, and tell
  the maintainer that new sign-in code is untested on a real site. If a second
  site needs identical code, raise it with the maintainer rather than moving it.
- On failure save to the run folder: screenshot, `fail_NN_page.txt`
  (`save_page_evidence`, a shadow-DOM-piercing outline), and `trace.zip`.
- Respect sites: one page load per item per run, no parallel requests.

## Testing

- `uv run pytest` and `uv run ruff check .` from here. Unit tests use
  synthetic fixtures only (e.g. a made-up HTML page mirroring the real
  structure). No test touches the network or a real site.
- Don't run anything against real sites unless asked.

## Versions, branches, and releases

- Branches `feature/<process>-<what>`, PR into `main`.
- The robot currently runs `main`: merging a PR is the release. To deploy, the
  maintainer runs `git pull` and `uv sync` on the robot.
- Release tags are on hold. Don't create or propose them, or write steps that
  depend on them, until the maintainer brings them back.
- Each process keeps a `CHANGELOG.md`, with new entries under `Unreleased`.

## Fixing a site change

1. The maintainer copies a failed run's `fail_*` files and `report.txt` /
   `run.log` into `scripts/automation/triage/` (git-ignored).
2. Read them: which item and step failed, what the page outline shows now.
3. Update the selector candidates (add the new one first; keep old ones
   unless they now match the wrong element). Update the synthetic fixture to
   mirror the new structure, and add a test that fails without the fix.
4. Run `uv run pytest`, commit, and ask the maintainer to rerun (for page
   structure questions, ask for a `--dump` run if the process has one).
5. Never commit anything from `triage/`.
