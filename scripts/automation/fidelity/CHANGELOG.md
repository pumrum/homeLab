# Changelog: fidelity-nav

Versions follow [Semantic Versioning](https://semver.org/). Release tags are
`fidelity/vX.Y.Z`.

## Unreleased

- Log timestamps use a period before the milliseconds
  (`12:47:49.275`, not `12:47:49,275`).
- The webhook gets only `title` ("fidelity") and `message` ("PASS" or
  "FAIL") instead of the full run summary, which stays in the run folder.

## 0.1.0 (unreleased)

- First version: reads each configured fund's NAV ("NAV" or "Unit Value")
  and as-of date from its summary page and appends it to `data\nav.csv`,
  one row per fund per as-of date.
- Parser tries the Daily Info header, then the Details row, then a text
  fallback; validates a positive NAV and an as-of date within 10 days.
- `--dump` saves each page's outline, screenshot, and HTML for fixing selectors.
