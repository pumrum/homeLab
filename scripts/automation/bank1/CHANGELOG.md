# Changelog: bank1-portal

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

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
