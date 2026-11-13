# Changelog: buildium-portal

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- Add `buildium-gl`: for each entity it signs in, exports the general ledger as CSV for each
  accounting method, and cleans it. Once every entity has worked, it replaces the master tab in a
  Google Sheet. Writing an entity's Cash view to its own tab is off unless that entity's
  `gl_write_tab` is `true`, because the sheet now builds those rows from the master. The first
  failure stops the run and writes nothing. A run that writes opens each tab it will write
  before signing in, so a wrong or deleted spreadsheet fails in seconds. `--dump` also saves the page structure and a masked profile of each export, and `--dump` and
  `--entity` never write. It needs a new `[gl]` section in `config.toml`, and `gl_` values in
  each `[[entities]]` block.
- Fix sign-out failing when the Account button's tooltip covers `Sign out`. It now moves the
  mouse off the button and escalates the click, which also covers `buildium-statements`.
- Fix every sign-in failing with `no Account menu` since the site moved its sign-in form to a
  separate sign-in server. The app address it passes through on the way looked like a session
  left open. The app now counts only once its Account menu shows, and the new form's fields and
  `Sign in` button are found first. This covers every command in this package.
- `buildium-gl` now also saves every export to `data\ledgers\<key>_<method>.csv`, replacing the
  last run's files, whenever it writes the sheet. Empty exports replace nothing, and each saved
  file must read back the same as the export. The run folder keeps its own copy.
- A sign-in the site refuses now fails as soon as the sign-in server shows its error. A refusal
  by its reCAPTCHA says so, instead of `the site did not accept the email and password`.
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
