# bank1-portal

Automations for a bank's customer website. Every command in this package shares one sign-in
flow, one browser profile, and one config file. One login reaches every account, personal and
business.

The site's addresses, the account names, and their numbers are only in the private config.
Everywhere else, accounts are known by neutral labels.

| Command | What it does |
| --- | --- |
| `bank1-statements` | Downloads each account's newest PDF statements, and its letters |

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

**Has run** (on the test workstation):

- A full run of all 11 accounts, clean: one sign-in with a remembered device, every account's
  statements page opened by its address and checked against its heading, every download (the
  checking menu's standard PDF, and the card and investment download links), every PDF check,
  and one sign-out
- Investments' **Single** and **Multi** statements and their letters, with the letters' file
  names from their titles
- `--dump` on one account of each kind

**Not yet run:**

- Sign-in and verification through this package's code, including clicking **Confirm using our
  mobile app** and **Next**. The option's selectors come from a `--dump` outline of the real
  chooser. Next's are candidates modeled on the site's other buttons. Nothing has clicked either
  on the real site yet
- Looking back a year when the newest year has no statement. The year filter's selectors come
  from the outline, but it has never been clicked
- Verification timing out

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

Fill in `login_url`, `name_in_files`, and for each account its `statements_url` and `suffix`. Copy each
statements address from Edge's address bar while that account's statements page is open.

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
statements a run would save, and how many letters it would download. It downloads nothing, and clicks nothing on those pages but the year
filter, when the newest year has no statement yet. It also saves the sign-in, verification
chooser, Next, after-push, and dashboard pages as `dump_site_<step>`. The sign-in page is saved
before anything is typed.

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

## Add another command

1. Add a module with its own `main()`, and a line under `[project.scripts]` in
   `pyproject.toml`.
2. Use `session.open_browser` once, and `session.signed_in_page` once. They handle the profile,
   credential, verification push, trace, sign-out, and failure evidence. Don't sign in any other
   way.
3. Pass `command="<command>"` to `Run.start`, so its notifications name the command.
4. Add its settings as a new section in `config.py`. `[site]` and `[[accounts]]` are already
   shared.
