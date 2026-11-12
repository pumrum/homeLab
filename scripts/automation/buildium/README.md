# buildium-portal

Automations for a property management site. Every command in this package shares one sign-in
flow, one browser profile, and one config file. Each entity has its own login.

The site's address, the entity names, and their abbreviations are only in the private config.

| Command | What it does |
| --- | --- |
| `buildium-statements` | Downloads each entity's rental owner statement for the last period |
| `buildium-gl` | Exports each entity's general ledger, cleans it, and replaces it in a Google Sheet |

The commands run separately, each with its own job and schedule. They share a browser profile,
but the agent runs one job at a time, so scheduled runs can't collide. Don't start one by hand
while the other is running.

## buildium-gl

For each entity in the config, one at a time, a run signs in, opens the General Ledger report, sets
a custom date range from 1/1/1999 to today, exports **Cash** and then **Accrual** as CSV, signs
out, and cleans both exports. Only once every entity has worked does it write to the sheet.

```powershell
uv run buildium-gl                    # normal run: every entity, then write the sheet
uv run buildium-gl --entity entity1   # only one entity; writes nothing
uv run buildium-gl --dump             # every entity, plus page copies and profiles; writes nothing
```

### What it writes

Each tab is a full snapshot, replaced every run.

| Tab | Rows | Before writing |
| --- | --- | --- |
| Each entity's tab, from `gl_start_cell`, only when its `gl_write_tab` is `true` | The Cash view: 15 columns, with the rules below | Clears from `gl_start_cell` to the tab's right edge and bottom |
| The master tab, from `A2` | Every entity, Cash and Accrual: 56 columns | Clears everything below row 1, then deletes spare rows so the tab shrinks with its data |

Entity tabs are off by default, because the sheet builds each entity's rows from the master with
formulas. A run still builds each entity's Cash view and reports its counts. To have a run write
one entity's tab again, for example while its formulas are broken, add `gl_write_tab = true` to
that entity's block, without quotes. Each entity's `gl_tab` and `gl_start_cell` are still
required and checked, so turning a tab on needs nothing else.

The master's columns are `Entity`, `Method`, the export's 53, and `EntryDate_YM`. Its row 1 is
yours and is never touched.

| Column | Written as |
| --- | --- |
| `Amount` | A number |
| `EntryDate` | A date, shown as `yyyy-mm-dd` |
| `EntryDate_YM` | The formula `=LEFT(INDEX(F:F, ROW()), 7)`, which reads `EntryDate` in column F |
| `EFTResultDate` | A date, shown as `m/d/yyyy` |
| Any other value that's a plain number, in any column | A number |
| `True` and `False`, in any column | `TRUE` and `FALSE`, as real true or false values |
| Everything else | Literal text, exactly as exported, so a memo starting with `=` can't run |

This matches paste values, except where a paste would change the value: `007` and numbers over 15
digits stay text. Sheets marks text that looks like a number or date with a leading `'` in the
formula bar. The `'` isn't part of the value.

### How rows are cleaned

Both methods:

1. The columns must be the expected 53 in order, ignoring case. A change fails with the names of
   what's missing and added.
2. Rows with no `Id` are report lines and are dropped, but one that isn't zero fails the run,
   because dropping it would change the totals.
3. Each entry in an entity's `gl_unit_fixes` sets the unit number on one lease's rows before its
   date.
4. Rows sort by `EntryDate`, `Id`, `PostingId`, `GLAccountNameRaw` descending, then `UnitNumber`.
   `Id` and `PostingId` sort as numbers.

The Cash view, in this order. The rules are in `gl_rules.py`, and they ignore case and outer spaces
because the export really has labels like `Check` with a trailing space:

1. A `Deposit Transfer` row with a payee becomes type `Equity`.
2. Every amount's sign flips.
3. Rows are removed by these rules, with the counts in `report.txt`:

| Rule | Removes rows where |
| --- | --- |
| Asset | `GLAccountTypeName` is `Asset` |
| Convenience fee | `GLAccountNameRaw` is `Convenience Fee` |
| Applied deposit | `JournalCodeDescription` is `Applied Deposit` |
| Deposit refund | `JournalCodeDescription` is `Refund` and `GLAccountTypeName` isn't `Income` |
| Owner draw by check | `GLAccountName` is `Owner Draw` and `JournalCodeDescription` is `Check` |
| Opening balance equity | `GLAccountName` is `Opening Balance Equity` |
| Opening security credits | `GLAccountTypeName` is `Liability` and `JournalCodeDescription` is `Credit` |

### What a run does when something's wrong

The sheet needs every entity, so unlike `buildium-statements`, the first failure stops the run. It's
a deliberate exception to the usual rule of carrying on with the next item.

| Situation | Result |
| --- | --- |
| The spreadsheet or a tab it will write can't be opened, such as a wrong or deleted `spreadsheet_id` | Checked before the browser starts, so the run stops without signing in or exporting. `FAIL`. Skipped by `--dump` and `--entity`, which never write |
| Any entity's sign-in, export, or cleaning fails | The run stops and writes nothing. `FAIL` |
| Sign-out fails, even after the last entity | The run stops and writes nothing. `FAIL` |
| Two entities' exports share an entry | Writes nothing. `FAIL`. All entities share one browser, so one may be another's data |
| The same `Id` and `PostingId` appear twice in an export | Kept, and counted in `report.txt` |
| A tab is gone by the time the run writes | Writes nothing to any tab. `FAIL` |
| The sheet is shared as Viewer, not Editor | It opens, so the check passes, and the run fails when it writes. `FAIL` |
| One tab's write fails | The other tabs are still written, and `report.txt` names the one that failed. `FAIL` |
| The master's write fails partway | It holds new rows above old ones, with nothing cleared yet, until the next good run. `FAIL` |

### What each run folder holds

| File | Holds | Safe to share? |
| --- | --- | --- |
| `report.txt`, `run.log` | Results, with each export's size and time, and row counts | Yes |
| `gl_profile_<key>_<method>.txt` | `--dump` only: the export's structure, with values reduced to shapes such as `-9,999.99` | Yes |
| `downloads\<key>_<method>.csv` | The raw export, kept for good | No, it holds real data |
| `dump_<key>_<step>_page.txt`, `.png`, `.html` | `--dump` only: the page at each step | No, it holds real names and addresses |

### Set it up

Share the spreadsheet with the service account as an Editor. In
`C:\automation\buildium\config.toml`, add the `[gl]` section from `config.example.toml`, and the
`gl_` values to each `[[entities]]` block. To test, point `spreadsheet_id` at a copy of the sheet
with the same tab names.

Exporting, cleaning, and writing the master have run on the real site and the real sheet, both
by hand and started by the agent on the robot. So have turning on an entity's `gl_write_tab`, and
the sheet check stopping a run on a deleted spreadsheet before it signed in. **Not yet run:** a
write failing partway, which can't be forced safely.

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
| Already signed in, with the app's Account menu showing | Signs that session out first, because it may be another entity's |
| Sign-in form | Fills in the email and password once from `buildium/<key>`, then clicks `Sign in` |
| The form stays gone with no app yet | Treated as MFA. Sends the push and waits up to 5 minutes |
| The app, with its Account menu showing | Signed in |
| An app address with no Account menu yet | On the way to or from the sign-in server, so it keeps waiting |

**MFA works in an unattended run.** The site emails a link. When the push arrives, open the email
and the link, and the job's browser moves on by itself. If that's not done in time, the run ends
with `FAIL: MFA needed`, so run the job again or sign in with `--capture` at the desktop (step 3
below). The site currently remembers a device after one successful sign-in, so this should be
rare.

Allow enough time in the agent's limit, which stops a job instantly and with no evidence. Each
entity can wait 5 minutes, plus a minute or two for its work.

Sign-in, MFA, sign-out, the statement menus, and saving have all run on the real site,
unattended. MFA timing out hasn't, and there's no plan to force it, so expect a possible fix if
it ever happens. The code doesn't know the MFA page's structure, so anything that isn't the form
or the app counts as MFA.

In November 2026 the site moved the sign-in form to a separate sign-in server, with new field
names. On the way there, the browser passes through an app address with nobody signed in, which
used to look like a session left open and failed every run with `no Account menu`. Now the app
counts only once its Account menu shows.

The first real run with this found the form and sent the email and password once, and the sign-in
server refused them with `Captcha verification failed`. Its reCAPTCHA scores each sign-in without
showing a challenge, and it judged the job's browser to be automated. The run now stops at once
with `the site's bot check (reCAPTCHA) refused the sign-in`, instead of waiting 30 seconds and
blaming the password. Bot checks are never worked around, so a run can't sign in while the site
refuses it. **Not yet run:** a sign-in the server accepts, and MFA through the sign-in server.

The Account button's tooltip was seen covering `Sign out` when one sign-out timed out. Sign-out
now moves the mouse to the item first, then uses `click_until`, and `run.log` says which way the
click worked: `sign-out: signed out (Sign out via click)`, for example. **Not yet run:** the
forced and keyboard fallbacks, which only run if the normal click fails.

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

[[jobs]]
name = "buildium-gl"
command = "buildium-gl"
timeout_minutes = 45
```

`buildium-gl`'s limit allows four MFA waits, one stalled export, and the sheet writes.

## Add another command

1. Add a module with its own `main()`, and a line under `[project.scripts]` in
   `pyproject.toml`.
2. Use `session.open_browser` once, and `session.signed_in_page` for each entity. They handle
   the profile, credential, MFA push, trace, sign-out, and failure evidence. Don't sign in any
   other way. Stop the run on `SignOutError`.
3. Pass `command="<command>"` to `Run.start`, so its notifications name the command.
4. Add its settings as a new section in `config.py`. `[site]` and `[[entities]]` are already
   shared.
