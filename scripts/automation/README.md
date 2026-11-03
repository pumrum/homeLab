# Automations

Unattended Windows automations, one package per process, sharing
`automation_common`. Machine setup (robot and developer workstation):
[machine-prep.md](machine-prep.md). Standards: [CLAUDE.md](CLAUDE.md).

Start Claude Code in `C:\src\homeLab`, not a parent folder, so it loads the
repo's rules and permissions from the start. A good first prompt for a new
automation: "Read `scripts/automation/CLAUDE.md`, then build an automation that
does X."

| Folder | What |
| --- | --- |
| `common/` | `automation_common`: run folder and summary, config, secrets, browser, Google Sheets, notify |
| `<process>/` | One package per automation, with its own README, config example, and changelog |
| `tools/env_check.py` | Machine check against FakeBank, a fake site served on 127.0.0.1 |
| `tools/import_secret_file.py` | Encrypts a large secret, like a Google service account key, for the current Windows user |

## Set up and test

```powershell
cd C:\src\homeLab\scripts\automation
uv sync                          # every member + dev tools, exact versions from uv.lock
uv run pytest                    # unit tests, synthetic data only
uv run ruff check .
uv run python tools\env_check.py           # Edge, policies, Credential Manager, FakeBank flow
uv run python tools\env_check.py --lock    # same, with Windows locked
```

`env_check.py` also runs standalone from a repo clone before `uv sync`:

```powershell
cd C:\src\homeLab\scripts\automation\tools
uv run --no-project --with playwright --with keyring python env_check.py
```

Its output goes to `C:\automation\env-check\runs\<timestamp>\report.txt`.
WARN for Edge policies is expected on the developer workstation.

## Run an automation

Each process reads `C:\automation\<process>\config.toml` (copied from its
`config.example.toml`) and writes under `C:\automation\<process>\`. Set
`AUTOMATION_DATA_ROOT` to use a different root than `C:\automation`.

```powershell
uv run <process-command>         # the console script named in the process's pyproject.toml
```

Every run makes `C:\automation\<process>\runs\<yyyyMMdd_HHmmss>\` with
`run.log`, `report.txt`, `summary.json`, and on failure `fail_NN.png`,
`fail_NN_page.txt`, and `trace.zip`. Exit code 0 means every item was OK.
If the Credential Manager entry `automation/home-assistant-webhook` exists,
each run POSTs `{"title": "<process>", "message": "PASS"}` (or `"FAIL"`) to
it, and nothing else; the details stay in the run folder. A generic Home
Assistant webhook automation can pass both straight to a notification:

```yaml
actions:
  - action: notify.mobile_app
    data:
      title: "{{ trigger.json.title }}"
      message: "{{ trigger.json.message }}"
```

## Add a new automation

1. Copy an existing process folder to `<process>/` and rename it. The layout:

   ```
   <process>/
     pyproject.toml          project, package, and console script names;
                             depends on automation-common = { workspace = true }
     config.example.toml     placeholders only
     CHANGELOG.md
     README.md               what it does, config, run, schedule
     src/<package>/
       __init__.py           PROCESS = "<process>"
       __main__.py           python -m <package>
       main.py               config, run folder, per-item loop, summary
       fetch.py              browser or download step
       parse.py              pure transforms
       store.py              write the output
       config.py             config validation
       sheet.py              only if it copies data to a Google Sheet
     tests/                  synthetic fixtures only
   ```
2. Set `PROCESS = "<process>"` in `__init__.py`; that name is the data folder.
3. Add `"<process>"` to `members` and the package to `dependencies` and
   `[tool.uv.sources]` in the root `pyproject.toml`, and its tests to
   `testpaths`. Add the package to ruff's `known-first-party`.
4. `uv sync`, write the parser against a synthetic fixture, `uv run pytest`.
5. Short secrets go in Credential Manager as `<process>/<name>`; read them with
   `automation_common.secrets.get_credential`. Large ones, over 2,560 bytes, go
   in an encrypted file imported with `tools\import_secret_file.py`. For Google
   Sheets, use the shared `automation/google-service-account` key and
   `automation_common.sheets.open_worksheet`, and share the sheet with the
   service account as an Editor.
6. Branch `feature/<process>-<what>`, PR into `main`.

## Release

The robot runs `main`, so merging a PR is the release. Release tags are on hold.

1. Add the change to the process's `CHANGELOG.md` under `Unreleased`.
2. After the PR is merged, on the robot as `robot`:

   ```powershell
   cd C:\src\homeLab; git switch main; git pull --ff-only
   cd scripts\automation; uv sync; uv run python tools\env_check.py
   ```

To roll back, check out the last good commit and run `uv sync` again.
