# Automation standards

Applies to everything under `scripts/automation/`. The repo-wide rules in the
root `CLAUDE.md` (US English, public repo, no bank names, ask before push/PR)
apply too.
Machine setup: [`machine-prep.md`](machine-prep.md).

## Machines and paths (Windows 11)

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

- Only through `automation_common.secrets` (keyring, i.e. Windows Credential
  Manager of the `robot` user). Entry names `<process>/<name>`; shared ones
  `automation/<name>` (e.g. `automation/home-assistant-webhook`).
- Never read, print, export, or store secret values, and never run `cmdkey` or
  `keyring` yourself. Pass the run's `masker` so values are hidden in logs.

## Code rules

- Fetch, transform, and write are separate modules. Transforms/parsers are
  pure functions with unit tests.
- Use `automation_common.run.Run`: it creates the run folder and log, and
  always writes the summary (`summary.json`, `report.txt`) on exit. If the
  `automation/home-assistant-webhook` secret exists, it POSTs the summary
  there; otherwise it only logs it.
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
- On failure save to the run folder: screenshot, `fail_NN_page.txt`
  (`save_page_evidence`, a shadow-DOM-piercing outline), and `trace.zip`.
- Respect sites: one page load per item per run, no parallel requests.

## Testing

- `uv run pytest` and `uv run ruff check .` from here. Unit tests use
  synthetic fixtures only (e.g. a made-up HTML page mirroring the real
  structure). No test touches the network or a real site.
- Live runs happen only on the maintainer's machine, by the maintainer.
  Don't run anything against real sites unless asked.

## Versions, branches, and releases

- Branches `feature/<process>-<what>`, PR into `main`.
- Tags per process: `<process>/vX.Y.Z` (SemVer). The robot runs only tagged
  releases. The maintainer creates tags; you may propose one.
- Each process keeps a `CHANGELOG.md` (newest first, `Unreleased` on top) and
  bumps `version` in its `pyproject.toml` with the release.

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
