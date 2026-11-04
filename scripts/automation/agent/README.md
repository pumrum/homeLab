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

## Set up

You need an MQTT broker, such as Home Assistant's Mosquitto add-on, and Home
Assistant's MQTT integration, plus a broker login for the robot. Sign in as the
user that will run the jobs (e.g. `robot`), then:

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

Fill in the broker's address and one `[[jobs]]` block per job. Use a different
`topic_prefix` on a test machine, such as `automation-test`, so it can't react to
the real schedule or disturb the robot's connection.

**3. Run it by hand and check:**

```powershell
uv run automation-agent
```

The window should say `connected to MQTT`. In Home Assistant, the **Automation
robot** device appears under the MQTT integration. Press a job's Run button and
watch its sensor go to `running` and then `PASS` or `FAIL`. Stop the agent with
Ctrl+C. Its log is `C:\automation\agent\logs\agent.log`.

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
