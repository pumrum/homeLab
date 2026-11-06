# Changelog: innago-portal

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- The push notifications' words can now be set in `config.toml`, as optional
  `[notifications.<event>]` blocks with `{variables}`. With no blocks, nothing changes.

## 0.1.0 (unreleased)

- First version. A shared sign-in handles a remembered profile, email and password,
  a passkey prompt (always declined), MFA (a timed wait), and a CAPTCHA (fails).
  A rejected email or password is never sent again.
- `innago-invoices` switches the list to `Not Grouped`, filters to the current month if
  it doesn't show one invoice per listed unit, and reads each row's unit and invoice
  number. It opens each invoice's own page, clicks its Download button, saves the PDF
  with `capture_pdf`, which handles it opening in a new tab, and names each one
  `<due date>_<unit>_<invoice number>-1.pdf` from the PDF's text. The naming logic
  comes from `scripts/innago_renameInvoices.py`, except that a unit or invoice number
  must now contain a digit, so labels like `Invoice Date` can't be taken for values.
- A run fails if the list is partial, a listed unit has no invoice, a PDF is for an
  unknown unit, or a unit has two invoices.
- Running again adds nothing. Same text is `unchanged`. Changed text replaces the file
  and keeps the old copy in the run folder.
- `--dump` saves page structure and reports the names, saving nothing under `data`.
- `--capture` saves page structure while a person signs in and clicks around.
- The passkey and MFA steps aren't yet checked against the real site, which hasn't shown
  them to the job. Everything else has run there.
