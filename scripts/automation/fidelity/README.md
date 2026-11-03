# fidelity-nav

Opens each configured fund's summary page in Edge, reads the latest NAV
(labeled "Unit Value" on 529 portfolio pages) and its "as of" date, and
appends it to `C:\automation\fidelity\data\nav.csv`. No sign-in and no
secrets. One page load per fund, one fund at a time.

## Config

`C:\automation\fidelity\config.toml`, copied from
[`config.example.toml`](config.example.toml). One `[[funds]]` block per fund:

```toml
[[funds]]
symbol = "<SYMBOL>"     # capital letters and digits, used in the CSV
url = "https://<fund summary page>"
```

The run stops with a clear message if the file is missing, invalid, or still
has the example's placeholders.

## Data file

`C:\automation\fidelity\data\nav.csv`, UTF-8:

```
as_of_date,symbol,nav,retrieved_at,source_url
2026-11-02,TEST1,12.34,2026-11-03T08:00:05-05:00,https://example.invalid/fund/TEST1
```

- One row per (symbol, as_of_date). Reading the same date and NAV again adds
  nothing (logged "unchanged").
- Same date, different NAV: a new row is appended with a warning; the latest
  row for a date wins.
- Append only: rows are never rewritten; the header is written only when the
  file is created.
- A fund fails (and the others continue) if the NAV isn't a positive number
  or the as-of date is in the future or more than 10 days old.

## Test on the workstation

From a fresh shell (needs uv and Edge; see [machine-prep.md](../machine-prep.md)):

```powershell
cd C:\src\homeLab
git fetch origin
git switch feature/fidelity-nav        # or: git pull, if already on it
cd scripts\automation
uv sync
New-Item -ItemType Directory C:\automation\fidelity -Force | Out-Null
Copy-Item fidelity\config.example.toml C:\automation\fidelity\config.toml
notepad C:\automation\fidelity\config.toml   # put in the real fund symbol and URL
uv run python tools\env_check.py
uv run fidelity-nav
Get-Content C:\automation\fidelity\data\nav.csv
```

Run `uv run fidelity-nav` a second time: the funds should report `unchanged`
and the CSV should not grow.

If a fund fails, look in the newest `C:\automation\fidelity\runs\<timestamp>\`
folder: `report.txt`, `run.log`, `fail_NN.png`, `fail_NN_page.txt`, `trace.zip`.

## Dump mode (fixing selectors)

```powershell
uv run fidelity-nav --dump
```

Loads each fund page and saves `dump_<SYMBOL>.png`, `dump_<SYMBOL>_page.txt`
(page outline) and `dump_<SYMBOL>.html` to the run folder, and reports what
the parser would read. Writes nothing to the CSV. Copy the files into
`scripts\automation\triage\` (git-ignored) to have selectors updated.

## Schedule (robot only)

Register as the `robot` user (see machine-prep B6), from a tagged release:

```powershell
$proj = "C:\src\homeLab\scripts\automation"
$a = New-ScheduledTaskAction -Execute "$proj\.venv\Scripts\fidelity-nav.exe" -WorkingDirectory $proj
$t = New-ScheduledTaskTrigger -Daily -At 19:30     # after the day's NAVs are published
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
  -MultipleInstances IgnoreNew -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "fidelity-nav" -Action $a -Trigger $t -Settings $s
```

Never schedule it on the developer workstation.
