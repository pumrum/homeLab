# automation-agent

Lets Home Assistant schedule and start jobs on the robot over MQTT. A small
listener stays signed in with the `robot` user and starts a job when asked, in
that user's desktop session, which Edge needs. Windows Task Scheduler only starts
the listener at sign-in.

- Home Assistant gets a **Run** button and a **state** sensor per job, automatically
  through MQTT discovery. The sensor shows `idle`, `running`, `PASS`, or `FAIL`.
- Only jobs listed in the private config can run. A message's content is never
  used beyond the word `run`, so nothing on the broker can run other commands.
- One job runs at a time. A press while that job is running or waiting is ignored.
- Jobs send their own PASS or FAIL notification as usual. If a job hangs past its
  limit or can't start, the agent stops it and sends the FAIL instead.
- If the robot is off or the agent is down when a schedule fires, that run is
  **missed**, not caught up. The sensor shows unavailable; see the optional alert.

| Topic | Use |
| --- | --- |
| `<prefix>/<job>/run` | Send `run` to start the job. Don't retain it |
| `<prefix>/<job>/state` | `idle`, `running`, `PASS`, or `FAIL` |
| `<prefix>/agent/status` | `online` or `offline` |

## Prepare the broker

These steps are for Home Assistant's Mosquitto broker, and start with plain MQTT on
port 1883. Only do this on a private network: never forward an MQTT port on your
router. The login and messages aren't encrypted, so give the robot a login that's
used for nothing else. To add protection later, see [Harden the
connection](#harden-the-connection).

**1. Check the port.** Under the broker's **Network** settings, keep 1883 set. The
other ports aren't used yet, so you can clear them.

**2. Create a login just for the robot.** In Home Assistant, go to **Settings >
People > Users** and add a non-admin, local-only user with a long random password.

**3. Check the connection.** Confirm the MQTT integration shows as connected.

## Set up

Sign in as the user that will run the jobs (e.g. `robot`), then:

**1. Store the MQTT login.** The prompt hides what you type:

```powershell
cd C:\src\homeLab\scripts\automation
uv sync
uv run python -c "import getpass, keyring; keyring.set_password('automation/mqtt', input('MQTT user: '), getpass.getpass('MQTT password: '))"
```

**2. Create the config:**

```powershell
New-Item -ItemType Directory C:\automation\agent -Force | Out-Null
Copy-Item agent\config.example.toml C:\automation\agent\config.toml
notepad C:\automation\agent\config.toml
```

Fill in the broker's IP address or host name and one `[[jobs]]` block per job. Use a
different `topic_prefix` on a test machine, such as `automation-test`, so it can't
react to the real schedule or disturb the robot's connection. If you limited the
login, add matching lines for that prefix to `accesscontrollist` too.

**3. Run it by hand and check:**

```powershell
uv run automation-agent
```

The window should say `connected to MQTT`, after a warning that TLS is off. If it
says `can't connect`, the message gives the reason: check the address and port first.
With TLS on, a certificate verify failure means a wrong host name, or a private CA
without `ca_file`. A `connection refused` message from the broker usually means a
wrong login. In Home Assistant, the **Automation robot** device appears under the
MQTT integration. Press a job's Run button and watch its sensor go to `running` and
then `PASS` or `FAIL`, where it stays until the next run or an agent restart. Stop
the agent with Ctrl+C. Its log is `C:\automation\agent\logs\agent.log`.

**4. On the robot only, start it at sign-in:**

```powershell
$ws = "C:\src\homeLab\scripts\automation"
$a = New-ScheduledTaskAction -Execute "$ws\.venv\Scripts\automation-agent.exe" -WorkingDirectory $ws
$t = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\robot"
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "automation-agent" -Action $a -Trigger $t -Settings $s
Start-ScheduledTask -TaskName "automation-agent"
```

**5. Add the schedule in Home Assistant.** Go to **Settings > Automations & scenes
> Create automation > Edit in YAML**:

```yaml
alias: Run <job>
mode: single
triggers:
  - trigger: time
    at: "08:00:00"
actions:
  - action: mqtt.publish
    data:
      topic: automation/<job>/run
      payload: run
```

**6. Remove the job's old Task Scheduler entry,** if it has one, so it can't also
run on its own:

```powershell
Unregister-ScheduledTask -TaskName "<old task name>" -Confirm:$false
```

## Optional: alert when the robot can't take jobs

Missed runs are silent, so this tells you when the agent has been down for a while.
Check the sensor's real entity ID under the device first.

```yaml
alias: Automation robot unavailable
triggers:
  - trigger: state
    entity_id: sensor.<job_state_sensor>
    to: unavailable
    for: "00:10:00"
actions:
  - action: notify.mobile_app
    data:
      title: Automation robot
      message: Offline
```

## Harden the connection

Do these once the plain setup works, and try them on a test machine first. They need
config and broker changes only. Check the broker's **Documentation** tab too, since
its options can change between versions.

### Limit what the login can do

Set the broker's `customize` option to active, and create these two files in Home
Assistant's `/share/mosquitto/` folder. Every login needs an entry once this is on,
so keep the first two blocks or Home Assistant loses its connection.

`acl.conf`:

```text
acl_file /share/mosquitto/accesscontrollist
```

`accesscontrollist`, with `<robot user>` replaced by the robot's login:

```text
user addons
topic readwrite #

user homeassistant
topic readwrite #

user <robot user>
topic read automation/+/run
topic write automation/+/state
topic write automation/agent/status
topic write homeassistant/#
```

Restart the broker, and confirm the MQTT integration still shows as connected.

### Add TLS

With TLS on, the agent checks the broker's certificate and host name.

**1. Get a certificate** for the host name the robot will connect to, and put its
files in Home Assistant's `/ssl` folder. Use a host name, not an IP address.

- **A publicly trusted certificate (simplest):** for example from Home Assistant's
  Let's Encrypt app. Windows already trusts it, so the agent doesn't need `ca_file`.
  Your network must resolve the name to Home Assistant's local address.
- **Your own CA:** the certificate needs the host name as a subject alternative name
  and `serverAuth` usage. Copy the CA's public certificate (never its key) to
  `C:\automation\agent\mqtt-ca.crt` and set `ca_file`. Renew it before it expires.

  ```bash
  openssl ecparam -genkey -name prime256v1 -out privkey.pem
  openssl req -new -key privkey.pem -subj "/CN=<host name>" -out broker.csr
  printf 'subjectAltName=DNS:<host name>\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\n' > broker.ext
  openssl x509 -req -in broker.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -sha256 -extfile broker.ext -out fullchain.pem
  ```

**2. Turn TLS on at the broker.** In the broker's **Configuration**, set `certfile`
and `keyfile` to those file names (e.g. `fullchain.pem` and `privkey.pem`), and
restart it. Under **Network**, set 8883 too.

**3. Switch the agent.** In `C:\automation\agent\config.toml`, set `host` to the name
on the certificate, `port = 8883`, and `tls = true`, then restart the agent and check
that it connects without the TLS warning.

**4. Close the other ports.** Under **Network**, clear 1883, 1884, and 8884, leaving
only 8883. Home Assistant itself still connects over its internal network, as long
as the MQTT integration's broker is `core-mosquitto`. Check that first, and check
that no other device on your network uses 1883. Don't forward 8883 on your router.

## Add a job

Add a `[[jobs]]` block to `C:\automation\agent\config.toml` and restart the agent
(step below). Its button and sensor appear by themselves. Add a Home Assistant
schedule as in step 5.

## Update the code

The running agent locks its files, so stop it before `uv sync`, at a time when no
job is running:

```powershell
Stop-ScheduledTask -TaskName "automation-agent"
cd C:\src\homeLab; git switch main; git pull --ff-only
cd scripts\automation; uv sync
Start-ScheduledTask -TaskName "automation-agent"
```

Stopping the agent while a job runs leaves that job running without reporting its
state, so check the sensor first.

## Remove a job from Home Assistant

Discovery messages are retained on the broker. To remove a job's button and sensor,
use **Delete** on the device or entity under the MQTT integration, or publish an
empty retained message to its `homeassistant/.../config` topics.
