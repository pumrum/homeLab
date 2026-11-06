# buildium-portal

Automations for a property management site. Every command in this package shares one sign-in
flow, one browser profile, and one config file. Each entity has its own login.

The site's address, the entity names, and their abbreviations are only in the private config.

| Command | What it does |
| --- | --- |
| `buildium-statements` | Downloads each entity's rental owner statement for the last period |
| `buildium-gl` | Exports each entity's general ledger. Under construction: `--dump` only, and it writes to no sheet yet |

The commands run separately, each with its own job and schedule. They share a browser profile,
but the agent runs one job at a time, so scheduled runs can't collide. Don't start one by hand
while the other is running.

## buildium-gl

For each entity in the config, one at a time, `--dump` signs in, opens the General Ledger report,
sets a custom date range from 1/1/1999 to today, exports **Cash** and then **Accrual** as CSV, and
signs out. The first failure stops the run, and the remaining entities are recorded as not
processed. Nothing is written under `data`, or to any sheet.

```powershell
uv run buildium-gl --dump                   # every entity
uv run buildium-gl --dump --entity entity1  # only one entity
```

Each run folder holds:

| File | Holds | Safe to share? |
| --- | --- | --- |
| `report.txt`, `run.log` | Results, with each export's size and time | Yes |
| `gl_profile_<key>_<method>.txt` | The export's structure, with values reduced to shapes such as `-9,999.99` | Yes |
| `downloads\<key>_<method>.csv` | The raw export | No, it holds real data |
| `dump_<key>_<step>_page.txt`, `.png`, `.html` | The page at each step | No, it holds real names and addresses |

**Not yet run on the real site:** the report's address, date range, method choice, and export
steps. Their selectors come from the old process and haven't run under this code. The sign-in is
the one `buildium-statements` uses, which has.

## buildium-statements

For each entity in the config, one at a time, a run signs in, opens **Rental owner > Rental
owner ledger**, chooses **Statement > Last period ending the last day**, checks the PDF, saves
it, and signs out. Each file is saved as:

```text
C:\automation\buildium\data\statements\<yyyy-MM>\<period end>_<system>_Ledger_<abbreviation>.pdf
```

A run on November 5, 2026 saves `2026-10\2026-10-31_<system>_Ledger_<abbreviation>.pdf`.

- **The date is the last day of the previous calendar month.** It comes from the run's date,
  not the site.
- **A statement is checked before it's saved.** Its header must name this entity's
  `statement_name` and cover that whole month. All entities share one browser, so this is what
  stops one entity's statement from being saved under another's name.
- **Running again replaces the file.** Nothing is compared or kept. A statement that fails its
  check never replaces a good file.
- **It only clicks what it needs.** Send funds and the other Statement periods are on the same
  page, so every label must match exactly, with no fallback to a similar one.

### What a run does when something's wrong

| Situation | Result |
| --- | --- |
| The site asks for MFA | A push names the entity's abbreviation, then it waits 5 minutes. See below |
| MFA isn't finished in time | That entity fails. The next one still runs. The final notification says `FAIL: MFA needed` |
| Rejected email or password | That entity fails after 30 seconds. Never retried, so it can't lock the account |
| CAPTCHA challenge showing | That entity fails right away. Never worked around |
| No credential for an entity | That entity fails. The next one still runs |
| The PDF names another owner or period | Not saved. That entity fails, and the PDF stays in the run folder under `downloads\` |
| No PDF arrives | That entity fails. The next one still runs |
| Sign-out fails | The statement already saved stays, but every remaining entity fails, because the run can't tell who is still signed in |

**Every failure saves what's on screen** to the newest
`C:\automation\buildium\runs\<timestamp>\` folder, as `fail_NN.png` and `fail_NN_page.txt`, with
the reason in `report.txt` and `run.log`. A failure after sign-in also saves
`trace_<key>.zip`. Any failure means exit code 1 and a `FAIL` notification titled
`buildium-statements`.

These are the default words. `[notifications.<event>]` blocks in `config.toml` change them:
`pass`, `fail`, and `mfa_failed` at the end of a run, and `mfa_needed` during it. Only
`mfa_needed` can use `{abbreviation}` and `{key}`, so the rest never name an entity. An
abbreviation goes to Home Assistant and your phone in that push, and stays masked in the run
folder. See `config.example.toml`.

## How sign-in works

| Page | What happens |
| --- | --- |
| Already signed in | Signs that session out first, because it may be another entity's |
| Sign-in form | Fills in the email and password once from `buildium/<key>`, then clicks `Sign in` |
| The form stays gone with no app yet | Treated as MFA. Sends the push and waits up to 5 minutes |
| The app | Signed in |

**MFA works in an unattended run.** The site emails a link. When the push arrives, open the email
and the link, and the job's browser moves on by itself. If that's not done in time, the run ends
with `FAIL: MFA needed`, so run the job again or sign in with `--capture` at the desktop (step 3
below). The site currently remembers a device after one successful sign-in, so this should be
rare.

Allow enough time in the agent's limit, which stops a job instantly and with no evidence. Each
entity can wait 5 minutes, plus a minute or two for its work.

Sign-in, MFA, sign-out, the statement menus, and saving have all run on the real site,
unattended. MFA timing out and a failed sign-out haven't, and there's no plan to force them, so
expect a possible fix if one ever happens. The code doesn't know the MFA page's structure, so
anything that isn't the form or the app counts as MFA.

## Set up each machine

Do these signed in as the Windows user that runs the job, in
`C:\src\homeLab\scripts\automation`.

**1. Store each entity's sign-in.** Run this once per entity, with its key in place of
`entity1`. The prompt hides the password:

```powershell
uv run python -c "import getpass, keyring; keyring.set_password('buildium/entity1', input('Email: '), getpass.getpass('Password: '))"
```

**2. Create the config:**

```powershell
New-Item -ItemType Directory C:\automation\buildium -Force | Out-Null
Copy-Item buildium\config.example.toml C:\automation\buildium\config.toml
& "C:\Program Files\Notepad++\notepad++.exe" C:\automation\buildium\config.toml
```

Add one `[[entities]]` block per entity. `statement_name` must match the name in the statement's
header, though case and extra spaces don't matter.

**3. Optional: sign in by hand.** A first run doesn't need this, because it asks for MFA with a
push. It's the fallback if a job's MFA doesn't go through. A sign-in in Edge from the Start menu
doesn't count, because the job has its own profile:

```powershell
uv run buildium-statements --capture
```

Sign in, finish MFA if the site asks for it, and sign out. Repeat for each entity, then close the
window. On the test workstation, MFA on the first entity covered the others.

## Run

```powershell
uv run buildium-statements --capture          # by hand: you sign in and click, page structure is saved
uv run buildium-statements --dump             # signs in and reports names; saves nothing under data
uv run buildium-statements --entity entity1   # only one entity
uv run buildium-statements                    # normal run
```

`--capture` and `--dump` save page outlines, screenshots, and for `--dump` the page's HTML to
the run folder. These hold real names and addresses. Replace private values before sharing any
of them, and never commit them.

## Schedule (robot only)

Home Assistant starts it through [automation-agent](../agent/README.md). Add this to
`C:\automation\agent\config.toml` and restart the agent. The limit allows four entities to each
wait for MFA, so raise it if you add more:

```toml
[[jobs]]
name = "buildium-statements"
command = "buildium-statements"
timeout_minutes = 30
```

## Add another command

1. Add a module with its own `main()`, and a line under `[project.scripts]` in
   `pyproject.toml`.
2. Use `session.open_browser` once, and `session.signed_in_page` for each entity. They handle
   the profile, credential, MFA push, trace, sign-out, and failure evidence. Don't sign in any
   other way. Stop the run on `SignOutError`.
3. Pass `command="<command>"` to `Run.start`, so its notifications name the command.
4. Add its settings as a new section in `config.py`. `[site]` and `[[entities]]` are already
   shared.
