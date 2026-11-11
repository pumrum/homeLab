# Changelog: bank1-portal

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- Add `bank1-statements`: it signs in once, verifying the device with a push if the site asks
  (choosing **Confirm using our mobile app** when it offers a choice), then saves each account's
  newest PDF statement as `data\statements\<label>\<label>_<YYYY-MM>.pdf`. Each account's
  statements page is opened by its direct address and checked against the account before
  anything downloads. Each PDF must show the account's suffix and closing date before it
  replaces a file. One account's failure doesn't stop the others. A verification timeout stops
  the run before any account is tried. `--dump` saves one statements page of each kind without
  downloading, and `--capture` saves snapshots while you click through by hand.
