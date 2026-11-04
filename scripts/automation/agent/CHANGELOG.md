# Changelog: automation-agent

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- First version: stays connected to MQTT and runs jobs named in its private
  config when asked, one at a time, with a time limit per job.
- Home Assistant discovery: a Run button and a state sensor per job.
- Reports FAIL itself when a job hangs past its limit or can't start.
- TLS on port 8883 unless the config sets `tls = false`, verifying the broker's
  certificate and host name, with an optional `ca_file` for a private CA. Connection
  failures log the reason.
- Docs and the example config start with plain MQTT on port 1883 on a private
  network, with an access list and TLS as later hardening steps.
- Accepts a config saved with a byte-order mark, through the shared loader. A file
  that isn't UTF-8 now gets a clear error.
- **Update** button and **Version** sensor in Home Assistant. A press starts a helper
  task that stops the agent, runs `git pull --ff-only` and `uv sync`, goes back to the
  previous commit if the sync fails, and starts the agent again. Refused while a job
  is running or waiting. Sends `PASS` or `FAIL`. Needs the helper task registered once.
- Logs each failed MQTT reconnect attempt, so a connection that doesn't come back is
  visible in the log instead of the log going quiet.
