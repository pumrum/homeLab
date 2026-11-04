# Changelog: invoices-sync

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

## 0.1.0 (unreleased)

- First version: downloads one list file per enabled entity over SFTP and pastes
  each file's lines as plain text into that entity's block on a Google Sheet tab,
  replacing the column from the start cell to the bottom of the tab in one request.
- Signs in with a private key held in Credential Manager and loaded only into
  memory. Verifies the server's host key against a `known_hosts` file.
- A missing, empty, or non-UTF-8 file fails that entity and leaves its block alone.
  The others still run, and the run exits 1.
- Keeps each downloaded file in the run folder under `files\`.
- `--dump` downloads the files and reports what was read, with no sheet write.
