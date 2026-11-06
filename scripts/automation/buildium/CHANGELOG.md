# Changelog: buildium-portal

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- Add `buildium-gl`, which so far only runs with `--dump`: for each entity it signs in, exports
  the general ledger as CSV for each accounting method, and saves the exports, the page
  structure, and a masked profile of each export to the run folder. It stops at the first
  entity that fails.
- `[statements]` is now only required by `buildium-statements`.
- The MFA push now names the entity's abbreviation instead of its key.
- The push notifications' words can now be set in `config.toml`, as optional
  `[notifications.<event>]` blocks with `{variables}`.

## 0.1.0 (unreleased)

- First version. A shared sign-in signs out any session still open, enters the email and
  password once, and waits 5 minutes for an emailed MFA link, sending a push when one is
  needed. A visible CAPTCHA fails the entity. A rejected password is never sent again.
- All entities share one browser profile and sign in one at a time. A failed sign-out stops
  the rest of the run, because it can't tell who is still signed in.
- `buildium-statements` opens the rental owner ledger, chooses the last period from the
  Statement menu, and saves the PDF with `capture_pdf` as
  `<period end>_<system>_Ledger_<abbreviation>.pdf`.
- A statement is only saved if its header names this entity and the previous calendar month.
  A run replaces any file already there.
- Every failure saves a screenshot and page outline to the run folder.
- `--dump` saves page structure and reports the names, saving nothing under `data`.
- `--capture` saves page structure while a person signs in and clicks around.
- `--entity` runs one entity, for testing.
- Tested on the real site, including an unattended run on the robot that completed MFA. MFA
  timing out and a failed sign-out haven't been tested there.
