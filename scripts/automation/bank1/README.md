# bank1-portal

Automations for a bank's customer website. Every command in this package shares one sign-in
flow, one browser profile, and one config file. One login reaches every account, personal and
business.

The site's addresses, the account names, and their numbers are only in the private config.
Everywhere else, accounts are known by neutral labels.

| Command | What it does |
| --- | --- |
| `bank1-statements` | Downloads each account's newest PDF statements, and its letters |
| `bank1-activity` | Downloads each account's activity as a CSV, and writes it to its Google Sheet tab |

The commands run on their own, as separate agent jobs, and on their own schedules. They share
the sign-in, the browser profile, the `bank1/login` credential, and `config.toml`, so a mistake
anywhere in that file stops both. The agent runs one job at a time, so they never share the
browser at once.

## bank1-statements

A run signs in once, then goes through the accounts in the config one at a time. For each, it
opens the account's statements page by its direct address, checks that it's this account's
page, finds its newest statements and any letters, downloads them, checks them, and saves them.
Then it signs out.

Every account's files go in one folder, `C:\automation\bank1\data\statements\`. They're named
the way this bank's statements were already being named, with the full closing date, the bank's
name from `name_in_files` in the config, and the account's four-digit suffix, so each account's
suffix must differ:

```text
2026-10-31_<name>_1234.pdf
2026-10-31_<name>_1234-single.pdf
2026-09-30_<name>_1234-single-2026-annual-disclosure-cover-letter.pdf
```

The kind of account sets which rows are its statements, and what each adds to the name:

| `kind` | Accounts | Statement rows | Added to the name |
| --- | --- | --- | --- |
| `checking` | Business and personal checking | **Statement** | Nothing |
| `card` | Personal credit cards | **Statement** | Nothing |
| `investment` | Personal investment | **Single**, this account alone, and **Multi**, which combines several accounts | `-single`, `-multi` |

- **The newest statements are the ones with the latest date,** wherever they are in the list.
  Rows that share a date sit in a group, and only the group's first row shows the date, so a
  row without one takes the date above it. An investment must have both its **Single** and
  **Multi** rows on that date.
- **Any other row is a letter,** like a disclosure or a notice. It's named after the statement
  row above it and its own title, so the copies filed under **Multi** and under **Single** both
  stay. Letters in the newest group are downloaded every run. Older ones are downloaded once,
  then left alone, since they don't change.
- **The list shows one year at a time,** newest first. If that year has no statement yet, as
  early in January, the run views the year before, once. Older statements aren't downloaded.
- **Every run downloads the newest statements again and replaces the files.**
- **Each file is checked before it replaces anything.** It must start with `%PDF`. A letter
  must be at least 1,000 bytes. A statement must be at least 5,000 bytes, and its text must
  show:
  - **A number ending in the account's `suffix`,** like `123-45678` for `5678`. An amount, like
    `5,678.00`, doesn't count.
  - **A period ending on the list's date or up to 5 days before it.** The list can show a
    statement a day or two after its period ends: a period of `October 01 - October 30, 2026`
    is listed as Oct 31. Only the period's end counts, since a statement also mentions the
    previous period's. A PDF with no period printed is checked against any date in it.

  A file that fails stays in the run folder under `downloads\`, and that file fails. The
  account's other files still go ahead.
- **Logs and reports never show a file name,** since it holds the bank's name and a suffix, or
  a letter's title. They show the label and a number instead: `<label> single`,
  `<label> letter 1`.

Each row is downloaded one of two ways, whichever it has:

- **A download menu,** as on checking accounts: the run opens it and chooses the standard
  **PDF**, never **Accessible PDF**. A menu without the standard PDF fails that file.
- **A download link,** as on cards and investments: the run clicks it.

Each row also has a control that opens the document for viewing. The run never uses it.
These are in `ROW_TYPES` and the selector lists in `statements_fetch.py`, and which rows to
save is in `plan_items` in `parse.py`.

### Checking it's the right account

Before anything downloads, the heading above the statements, like `<name> (...1234)`, must show
the account's `match` text. That's its `suffix` unless `match` is set, and a number must stand
alone, so `1234` doesn't match `91234`. Only the heading counts: the page's side menu lists
every account. If the heading doesn't show it, or the address leads to the dashboard, a
sign-in page, or no statement list, that account fails with `statements link no longer valid`,
and you need to update its `statements_url`. The run doesn't look for the page through the
site's menus. That could come later if links turn out to expire often.

### What a run does when something's wrong

| Situation | Result |
| --- | --- |
| The site asks to verify the device | Chooses **Confirm using our mobile app**, then **Next**. A push says `Approve the sign-in on your phone`, then it waits 5 minutes |
| The verification page has no **Confirm using our mobile app** option, or no **Next** after it | The run stops. No push is sent and no account is tried. `FAIL` |
| Verification isn't approved in time | The whole run stops. No account is tried and no file is touched. `FAIL: verification timed out` |
| Rejected username or password | The run stops after 45 seconds. Never retried, so it can't lock the login. `FAIL` |
| CAPTCHA challenge showing | The run stops right away. Never worked around. `FAIL` |
| No `bank1/login` credential | The run stops before opening the site. `FAIL` |
| A statements link no longer leads to that account's page | That account fails. The rest still run. `FAIL` |
| No statement row this year or last, or an investment missing **Single** or **Multi** on the newest date | That account fails. The rest still run. `FAIL` |
| A download menu without the standard PDF | That file fails. The rest still run. `FAIL` |
| No PDF arrives, or a statement names another account or date | Not saved. That file fails, and it stays in `downloads\`. `FAIL` |
| Sign-out fails | Only a warning in `run.log`. The statements are already saved, and the site ends idle sessions itself |

**Every failure saves what's on screen** to the newest `C:\automation\bank1\runs\<timestamp>\`
folder, as `fail_NN.png` and `fail_NN_page.txt`, with the reason in `report.txt` and `run.log`.
A failure after sign-in also saves `trace.zip`. A trace never starts before the sign-in is done.
Any failure means exit code 1 and a notification titled `bank1-statements`.

These are the default words. `[notifications.<event>]` blocks in `config.toml` change them:
`pass`, `fail`, and `mfa_failed` at the end of a run, and `mfa_needed` during it. See
`config.example.toml`.

## bank1-activity

A run signs in once, then goes through the accounts with an `activity_url` in the config, one at
a time. For each, it opens the account's activity download page by its direct address, checks
it's this account's form, chooses **Choose a date range**, types the range, clicks **Download**,
checks the CSV, saves it, and writes it to the account's tab. Then it signs out.

- **The range** runs from the account's `activity_start` to the day of the run. The site's
  download is current to the minute, so a run at 4 pm picks up what posted after an 8 am run.
- **Every run replaces the sheet's block.** The run clears everything from the account's
  `start_cell` down, across exactly as many columns as the CSV has, then writes the rows there.
  Columns beside the block are never touched. Nothing is appended or compared, so running again
  the same day, or from another machine, just repeats the overwrite.
- **Every run saves a new CSV** per account in `C:\automation\bank1\data\activity\`, named as the
  old process named them, with the run's start time:
  `<abbreviation>_<suffix>_<yyyymmdd-HHmmss>_Activity.csv`, like
  `ABC1_1234_20261113-143342_Activity.csv`. Older files are kept, so the folder grows by one file
  per account each run. Nothing clears it.
- **Oldest first, no header, as text.** The bank exports the newest first, so the rows are
  reversed. Every column is written, and every value goes in as literal text, exactly as
  exported, as the old process pasted it.
- **An account without `activity_sheet`** is downloaded, checked, and saved, but written nowhere.
- **Investment accounts** have no `activity_url` yet: whether the site has an activity download
  for them isn't known.

Each CSV is checked before it replaces anything. A file that fails stays in the run folder under
`downloads\`, the saved file and the sheet are left as they were, and that account fails:

- **Its header must be the one expected for its kind,** in `COLUMNS` in `activity_parse.py`. A
  change names the columns added, missing, or moved, so a new column on the site can't shift
  every column in the sheet.
- **It must hold at least one transaction,** so an empty download never clears a block.
- **Every row must be dated in the range,** by `Posting Date` for checking, or by either
  `Transaction Date` or `Post Date` for cards.

The account's page is checked like a statements page: the form's **Account** drop-down must show
the account's `match` (its `suffix` unless set). Only the account it shows counts, not the hidden
list of every account behind it. If the address leads anywhere else, that account fails with
`activity link no longer valid`, and you need to update its `activity_url`.

The choice is picked by its words, never its place in the list, since the list differs between
kinds. The dates are typed key by key, because the boxes show a pasted date but don't register
it. The **File type** must already show **Spreadsheet (Excel, CSV)**, the site's default. The run
never changes it. The selectors are in `activity_fetch.py`.

| Situation | Result |
| --- | --- |
| An activity link no longer leads to that account's form | That account fails. The rest still run. `FAIL` |
| No **Choose a date range** choice, or no date boxes after it | That account fails. `FAIL` |
| The File type shows something other than a CSV | That account fails before Download. `FAIL` |
| Download sends nothing within 60 seconds | That account fails. `FAIL` |
| The CSV's columns changed, it's empty, or a row is out of range | Not saved or written. That account fails, and the file stays in `downloads\`. `FAIL` |
| The sheet write fails | The CSV is still saved. That account fails. `FAIL` |
| `activity_start` is after today | That account fails before its page opens. `FAIL` |
| Sign-in, verification, or credential problems | As for `bank1-statements`: nothing is tried, and no file or sheet is touched |

The summary shows each account's row count and date span, and whether it was written, saved
only, or downloaded only. It never shows a tab name, a spreadsheet ID, or a transaction.

## How sign-in works

| Page | What happens |
| --- | --- |
| The profile is still signed in | Goes straight on, typing nothing |
| Sign-in form | Fills in the username and password once from `bank1/login`, then clicks Sign in. The fields may sit in an iframe or in shadow roots, so each is a list of candidates |
| Verification words on screen, twice in a row, with the form gone | Treated as device verification. If the page asks how to confirm, clicks **Confirm using our mobile app**, never **Sign into our mobile app**, then **Next**, which has the site send its push. Then sends a notification and waits up to 5 minutes for the dashboard |
| The dashboard (`dashboard` in its address) | Signed in. Waits for it to finish loading before opening any statements link |

The sign-in page often mentions the bank's mobile app, so the verification words count only
once the form has gone. They also count only on two checks in a row, and never once the
dashboard is there.

The profile is under `C:\automation\bank1\profiles\edge`. After one approved verification, the
site remembers the device, so later runs usually aren't asked. Never copy a profile between
machines: each one verifies once.

Allow enough time in the agent's limit, which stops a job instantly and with no evidence. A run
can wait 5 minutes for verification, plus up to about 2 minutes per account.

## What has and hasn't run on the real site

It was built from a working smoke test, which signed in, verified the device, and downloaded
an activity CSV, unattended and on a locked session. The sign-in selectors and verification
words come from that test.

**Has run:**

- On the test workstation, a full run of all 11 accounts, clean: one sign-in with a remembered
  device, every account's statements page opened by its address and checked against its
  heading, every download (the checking menu's standard PDF, and the card and investment
  download links), every PDF check, and one sign-out
- Investments' **Single** and **Multi** statements and their letters, with the letters' file
  names from their titles
- `--dump` on one account of each kind
- On the robot, unattended through the agent: a full run with a new profile, verifying the
  device by clicking **Confirm using our mobile app** and **Next**, sending the push, and
  going on once it was approved

**Not yet run:**

- Looking back a year when the newest year has no statement. The year filter's selectors come
  from the outline, but it has never been clicked
- Verification timing out

**`bank1-activity` has run on the real site:**

- `--dump` on the test workstation for one checking and one card account: one sign-in with a
  remembered device, each form opened by its address and checked against its Account drop-down,
  **Choose a date range** chosen, both dates typed and read back, the File type showing a CSV,
  and one sign-out

**Not yet run:**

- A download. The checking header comes from the smoke test. The card header is a guess:
  `Transaction Date, Post Date, Description, Category, Type, Amount, Memo`. The first card
  download will name any difference
- Which date the site's range filters cards by
- A sheet write. Whether the old process's writes landed as text is also unknown:
  `=ISTEXT()` on an amount it wrote shows it
- Verification timing out, and a link that no longer leads to the form

## Set up each machine

Do these signed in as the Windows user that runs the job, in
`C:\src\homeLab\scripts\automation`.

**1. Store the login.** The prompt hides the password. Paste this, then press Enter:

```powershell
uv run python -c "import getpass, keyring; keyring.set_password('bank1/login', input('Username: '), getpass.getpass('Password: '))"
```

**2. Create the config:**

```powershell
New-Item -ItemType Directory C:\automation\bank1 -Force | Out-Null
Copy-Item bank1\config.example.toml C:\automation\bank1\config.toml
& "C:\Program Files\Notepad++\notepad++.exe" C:\automation\bank1\config.toml

```

Fill in `login_url`, `name_in_files`, and for each account its `statements_url` and `suffix`.
Copy each statements address from Edge's address bar while that account's statements page is
open.

For `bank1-activity`, also fill in each account's `activity_url`, copied the same way from its
download account activity page, `activity_start`, and `abbreviation`. For each account with a
sheet, fill in its `activity_sheet`, and add a `[sheets.<name>]` block for each workbook. Share each workbook with
the service account as an Editor. The service account key is the same one other automations
use, `automation/google-service-account`, imported once per Windows user (see
[`machine-prep.md`](../machine-prep.md)). On the test workstation, point `[sheets]` at test
copies only, or leave every `activity_sheet` out to only download.

## Run

```powershell
uv run bank1-statements --capture                   # by hand: you sign in and click, page structure is saved
uv run bank1-statements --dump                      # signs in, saves one statements page of each kind
uv run bank1-statements --dump --account <label>    # the same, for one account
uv run bank1-statements --account <label>           # only one account
uv run bank1-statements                             # normal run
```

`--dump` signs in, then opens the first account of each kind (or the one named), saves its page
outline and screenshot as `dump_<label>_statements_page.txt` and `.png`, and reports which
statements a run would save, and how many letters it would download. It downloads nothing, and
clicks nothing on those pages but the year filter, when the newest year has no statement yet.
It also saves the sign-in, verification chooser, Next, after-push, and dashboard pages as
`dump_site_<step>`. The sign-in page is saved before anything is typed.

```powershell
uv run bank1-activity --dump                     # signs in, fills in one form of each kind
uv run bank1-activity --download-only            # downloads into the run folder only
uv run bank1-activity --account <label>          # only one account
uv run bank1-activity                            # normal run
```

`bank1-activity --dump` signs in, then for the first account of each kind (or the one named)
opens its form, chooses the date range, and types the dates, saving the page as
`dump_<label>_activity` and `dump_<label>_form`, `_page.txt` and `.png`. It never clicks
Download. `--download-only` does everything but save to `data\` and write the sheet: the CSV
stays in the run folder as `downloads\<label>.csv`, and the report shows its row count and dates.

`--capture` opens the job's browser on the sign-in page and saves a snapshot each time the page
changes, for up to 30 minutes or until you close the window. It types nothing and starts no
trace. Use it to record a page `--dump` can't reach, like an open download menu. It's also the
fallback for signing in by hand if a job's verification doesn't go through.

These files hold real account names and numbers. Replace private values before sharing any of
them, and never commit them.

## Schedule (robot only)

Home Assistant starts it through [automation-agent](../agent/README.md). Add this to
`C:\automation\agent\config.toml` and restart the agent:

```toml
[[jobs]]
name = "bank1-statements"
command = "bank1-statements"
timeout_minutes = 40
```

The limit allows 5 minutes of verification and 11 accounts at their slowest. Raise it if you add
accounts.

```toml
[[jobs]]
name = "bank1-activity"
command = "bank1-activity"
timeout_minutes = 30
```

That allows 5 minutes of verification and 8 accounts at up to about 2 minutes each, plus the
sheet writes.

## Add another command

1. Add a module with its own `main()`, and a line under `[project.scripts]` in
   `pyproject.toml`.
2. Use `session.open_browser` once, and `session.signed_in_page` once. They handle the profile,
   credential, verification push, trace, sign-out, and failure evidence. Don't sign in any other
   way.
3. Pass `command="<command>"` to `Run.start`, so its notifications name the command.
4. Add its settings as a new section in `config.py`. `[site]` and `[[accounts]]` are already
   shared.
