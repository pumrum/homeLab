# invoices-sync

Each run downloads one list file per enabled entity from a Linux server over SFTP,
and pastes each file into that entity's block on a Google Sheet tab.

- **One file, one paste.** Each file goes in one request, like paste values, however
  many lines it has. Nothing is done row by row.
- **It overwrites on purpose.** The column is replaced from the entity's start cell
  to the bottom of the tab. Rows above the start cell and other columns aren't
  touched. Google applies each request completely or not at all, so a failed write
  leaves that block as it was.
- **Everything is text.** A line is never reinterpreted as a number, date, or formula.
- **There's no history or matching.** The file is the truth, so running again writes
  the same values and can't add duplicates.
- **It doesn't check freshness.** A run before the server refreshes its files writes
  the previous files with no warning.

## What a run does when something's wrong

| Situation | Result |
| --- | --- |
| Missing or empty file, or a file that isn't UTF-8 | That entity fails and its block stays as it is. The others still run |
| Can't connect, or the host key is unknown or changed | Every enabled entity fails and nothing is written |
| One block's write fails | That entity fails and the others still run. Run it again to fix it |
| A copy can't be saved to the run folder | The run fails, but the sheet write still happens |

Any failure means exit code 1 and a `FAIL` notification. The notification says `PASS`
or `FAIL` unless `[notifications.<event>]` blocks in `config.toml` change the words. Results for each entity are in the newest
`C:\automation\invoices\runs\<timestamp>\` folder, in `report.txt` and `run.log`,
with each downloaded file under `files\`.

## Config

`C:\automation\invoices\config.toml`, copied from
[`config.example.toml`](config.example.toml). Add one `[[entities]]` block per entity,
with no code change. Setting `enabled = false` skips that entity and leaves its block
alone. Two entities can't share a column, because each paste clears its column to the
bottom of the tab.

## Set up the server

1. Create a login for this job, with no password and no shell. Limit it in
   `sshd_config`, using its own `Match` block so other logins aren't affected:

   ```text
   Match User <sftp user>
           X11Forwarding no
           AllowTcpForwarding no
           PermitTTY no
           ChrootDirectory <folder>
           ForceCommand internal-sftp -R
   ```

   - `-R` makes the session read-only.
   - Every folder in the `ChrootDirectory` path must be owned by root and not writable
     by group or others.
   - `folder` in `config.toml` is the path as seen inside the chroot.
   - The chroot exposes everything under it, so give the login read permission on the
     list files only.
2. Add one line per machine to the login's `authorized_keys` file, using the public
   key from the next section. That file lives outside the chroot. The login must be
   able to read it, and only root or the login may be able to write to it. Then check:

   ```bash
   sudo sshd -t && sudo systemctl reload ssh
   ```

   ```bash
   sudo -u <sftp user> head -c 1 <full path to one list file> > /dev/null && echo "can read"
   ```

   ```bash
   sudo -u <sftp user> test -w <full path to one list file> && echo "CAN WRITE: fix this" || echo "can't write"
   ```

## Set up each machine

Do these signed in as the Windows user that runs the job, in
`C:\src\homeLab\scripts\automation`. Each machine gets its own key, so you can revoke
one without affecting the other. Never copy a key between machines.

**1. Make a key and store it in Credential Manager.** The key is only ever loaded into
memory when the job runs. The second command reads the file you name without printing
it. Credential Manager holds at most 2,560 bytes, and `keyring` stores two bytes per
character, so the limit is 1,280 characters. An Ed25519 key fits, but RSA keys of 3072
bits or more don't.

```powershell
ssh-keygen -t ed25519 -N '""' -C "invoices-<machine>" -f "$env:TEMP\invoices_key"
```

```powershell
uv run python -c "import keyring, pathlib; t = pathlib.Path(input('Private key file: ').strip().strip(chr(34))).read_text(encoding='ascii'); assert len(t) <= 1280, 'too long for Credential Manager'; keyring.set_password('invoices/ssh-key', '<sftp user>', t); assert keyring.get_password('invoices/ssh-key', '<sftp user>') == t; print('stored', len(t), 'characters')"
```

```powershell
Get-Content "$env:TEMP\invoices_key.pub"
```

Add the printed public key to the server's `authorized_keys`. This form of the line
also limits where the key can be used from:

```text
restrict,from="<this machine's IP address>" ssh-ed25519 AAAA... invoices-<machine>
```

Then delete both key files:

```powershell
Remove-Item "$env:TEMP\invoices_key", "$env:TEMP\invoices_key.pub"
```

To replace a key, delete the old entry first. Otherwise `keyring` keeps a copy of the
old key under a second name:

```powershell
uv run python -c "import keyring; keyring.delete_password('invoices/ssh-key', '<sftp user>')"
```

**2. Record the server's host key.** The job refuses a server whose key isn't in this
file. This scan uses `ssh` because `ssh-keyscan` on Windows can fail with
`unsupported KEX method` and write nothing. `ssh` saves the key before it tries to sign
in, and it offers no credentials, so expect `Permanently added` and then
`Permission denied`. Use the same host name or address as `host` in `config.toml`, and
leave out `-p` if the port is 22.

```powershell
New-Item -ItemType Directory C:\automation\invoices -Force | Out-Null
ssh -p <port> -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o HostKeyAlgorithms=ssh-ed25519 -o PreferredAuthentications=none -o UserKnownHostsFile=C:\automation\invoices\known_hosts <sftp user>@<host>
ssh-keygen -lf C:\automation\invoices\known_hosts
```

On the server, get the same fingerprint directly. The two must match, because this is
what proves that the scan reached the right server:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

If the server's key ever changes, delete `known_hosts` and repeat this step. The job
fails until you do.

**3. Share the sheet.** Share the Google Sheet with the service account as an
**Editor**, and see the repo's [README](../README.md) for the shared key. A test
machine should have its own service account, shared only on the test sheet, so it
can't reach the live sheet even if its config is wrong.

**4. Create the config.**

```powershell
Copy-Item invoices\config.example.toml C:\automation\invoices\config.toml
notepad C:\automation\invoices\config.toml
```

## Run

```powershell
uv run invoices-sync --dump   # download and report only; writes nothing to the sheet
uv run invoices-sync          # paste each file into its block
```

## Schedule (robot only)

Home Assistant schedules it through [automation-agent](../agent/README.md). Run it
after the server refreshes its files. Add this to `C:\automation\agent\config.toml`
and restart the agent:

```toml
[[jobs]]
name = "invoices"
command = "invoices-sync"
timeout_minutes = 10
```

Then add the schedule in Home Assistant:

```yaml
alias: Run invoices
mode: single
triggers:
  - trigger: time
    at: "<time>"
actions:
  - action: mqtt.publish
    data:
      topic: automation/invoices/run
      payload: run
```

The **Automation robot** device also gets an `invoices run` button for manual runs.
