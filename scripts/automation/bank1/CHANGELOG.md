# Changelog: bank1-portal

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- `bank1-activity` can stamp each workbook with when it was refreshed, as buildium does: with
  `updated_tab` and `updated_cell` in its `[sheets.<name>]` block, the cell gets the run's start
  time, shown as `yyyy-mm-dd hh:mm:ss`, once every account writing to that workbook this run
  has written. If one failed, the time is left as it was.
- Fix sign-in when the site asks to verify the device: it renamed the push option from
  **Confirm using our mobile app** to **Get a notification**, and moved its list of options. The
  run looks for the new words first, then the old ones, and never chooses **Open our mobile
  app**. This affects both commands.
- Add `bank1-activity`, to replace the old account-recon process: it signs in the same way, then
  for each account with an `activity_url` downloads its activity as a CSV from its
  `activity_start` to today. It checks the header for the account's kind, that there's at least
  one row, and that every row is in the range. Then it saves a new CSV in `data\activity\`,
  named `<abbreviation>_<suffix>_<yyyymmdd-HHmmss>_Activity.csv` from the run's start time, and
  writes the rows, oldest first, with no header, to its `activity_sheet`, as a paste of values
  would store them: plain numbers and `MM/DD/YYYY` dates as values, anything else as text. Each
  run clears the account's block from its start cell down, across only the CSV's columns. A file that fails its checks replaces nothing. `--dump` fills in one form
  of each kind without downloading, and `--download-only` keeps the CSV in the run folder.
  Config gains `[sheets.<name>]`, the `activity_` account keys, and `abbreviation`.
- `bank1-statements` and `bank1-activity` share their run plumbing in `cli.py`. Reading a
  page's text now includes the shadow root of the element read, not only its children's.
- Add `bank1-statements`: it signs in once, verifying the device with a push if the site asks
  (choosing **Confirm using our mobile app**, then **Next**, when it offers a choice), then
  saves each account's newest PDF statements: the **Statement** row, or for investments both
  **Single** and **Multi**. It also saves the letters in the list, like disclosures: the newest
  every run, older ones once. Files are named as this bank's statements already were, as
  `<YYYY-MM-DD>_<name>_<suffix>.pdf`, with `-single`, `-multi`, or a letter's title added when
  needed, all in `data\statements\`. Each account's statements page is opened by its direct
  address and checked against the heading above the list before anything downloads. Each
  statement PDF must show the account's suffix and closing date before it replaces a file. One
  file's failure doesn't stop the others. If the newest year has no statement yet, the run looks
  at the year before. A verification timeout stops the run before any account is tried. `--dump`
  reports what a run would save without downloading, and `--capture` saves snapshots while you
  click through by hand.
