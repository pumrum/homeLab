# Changelog: automation-agent

Versions follow [Semantic Versioning](https://semver.org/). The robot runs
`main`; release tags are on hold.

## Unreleased

- First version: stays connected to MQTT and runs jobs named in its private
  config when asked, one at a time, with a time limit per job.
- Home Assistant discovery: a Run button and a state sensor per job.
- Reports FAIL itself when a job hangs past its limit or can't start.
- TLS on port 8883 by default, verifying the broker's certificate and host name,
  with an optional `ca_file` for a private CA. Connection failures log the reason.
