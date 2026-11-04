# Changelog: fidelity-nav

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- Scheduled by Home Assistant through automation-agent instead of Task Scheduler.
- Accepts a config saved with a byte-order mark, through the shared loader. A file
  that isn't UTF-8 now gets a clear error.
- Log timestamps use a period before the milliseconds
  (`12:47:49.275`, not `12:47:49,275`).
- The webhook gets only `title` ("fidelity") and `message` ("PASS" or
  "FAIL") instead of the full run summary, which stays in the run folder.
- Optional Google Sheet: with a `[sheet]` section in `config.toml`, each run
  appends the `nav.csv` rows the sheet doesn't have yet (as_of_date, symbol,
  nav, retrieved_at), using a service account key stored encrypted for the
  Windows user.

## 0.1.0 (unreleased)

- First version: reads each configured fund's NAV ("NAV" or "Unit Value")
  and as-of date from its summary page and appends it to `data\nav.csv`,
  one row per fund per as-of date.
- Parser tries the Daily Info header, then the Details row, then a text
  fallback; validates a positive NAV and an as-of date within 10 days.
- `--dump` saves each page's outline, screenshot, and HTML for fixing selectors.
