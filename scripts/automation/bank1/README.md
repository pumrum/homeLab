# bank1-portal

Automations for a bank's customer website. Every command in this package shares one sign-in
flow, one browser profile, and one config file. One login reaches every account, personal and
business.

The site's addresses, the account names, and their numbers are only in the private config.
Everywhere else, accounts are known by neutral labels.

| Command | What it does |
| --- | --- |
| `bank1-statements` | Downloads each account's newest PDF statement |

## bank1-statements

A run signs in once, then goes through the accounts in the config one at a time. For each, it
opens the account's statements page by its direct address, checks that it's this account's
page, finds the newest statement, downloads it, checks it, and saves it as:

```text
C:\automation\bank1\data\statements\<label>\<label>_<YYYY-MM>.pdf
```

`YYYY-MM` is the month the statement closed, read from the statement list on the page. A run on
November 5, 2026 saves `entity1_checking\entity1_checking_2026-10.pdf`, for example. Then it signs
out.

- **The newest statement is the row with the latest date,** wherever it is in the list. A row
  showing a period, like `Oct 1, 2026 - Oct 27, 2026`, closes on its last date.
- **Only the newest is downloaded.** Earlier files already on disk are left alone.
- **Every run downloads again and replaces the file.** Nothing is compared or kept.
- **A statement is checked before it replaces anything.** It must start with `%PDF`, be at least
  5,000 bytes, and its text must show the account's four-digit `suffix` and the closing date
  from the list. A file that fails stays in the run folder under `downloads\`, and that account
  fails.
- **If another statement closed in the same month as the newest,** it's saved under the same
  name, so the newest replaces it. `report.txt` notes how many there were.

Each kind of account has one small extra step:

| `kind` | Accounts | Extra step |
| --- | --- | --- |
| `checking` | 4 business checking, 2 personal deposit | After the statement's link, choose **Standard** PDF, not Accessible, then Download |
| `card` | 2 personal credit cards | None |
| `investment` | 2 personal investment | Before picking the statement, choose **Single** statement, not Multiple |

A missing choice fails that account. The steps are in `KIND_STEPS` in `statements_fetch.py`.

### Checking it's the right account

Before anything downloads, the statements page must show the account's `match` text. That's its
`suffix` unless `match` is set, and a number must stand alone, so `1234` doesn't match `91234`.
If the page doesn't show it, or the address leads to the dashboard, a sign-in page, or no
statement list, that account fails with `statements link no longer valid`, and you need to
update its `statements_url`. The run doesn't look for the page through the site's menus.
That could come later if links turn out to expire often.

### What a run does when something's wrong

| Situation | Result |
| --- | --- |
| The site asks to verify the device | Chooses **Confirm using our mobile app**, a push says `Approve the sign-in on your phone`, then it waits 5 minutes |
| The verification page has no **Confirm using our mobile app** option | The run stops. Nothing is chosen and no account is tried. `FAIL` |
| Verification isn't approved in time | The whole run stops. No account is tried and no file is touched. `FAIL: verification timed out` |
| Rejected username or password | The run stops after 45 seconds. Never retried, so it can't lock the login. `FAIL` |
| CAPTCHA challenge showing | The run stops right away. Never worked around. `FAIL` |
| No `bank1/login` credential | The run stops before opening the site. `FAIL` |
| A statements link no longer leads to that account's page | That account fails. The rest still run. `FAIL` |
| An expected Standard or Single choice is missing | That account fails. The rest still run. `FAIL` |
| No PDF arrives, or the PDF names another account or date | Not saved. That account fails, and the file stays in `downloads\`. `FAIL` |
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
| Verification words on screen, twice in a row, with the form gone | Treated as device verification. If the page asks how to confirm, clicks **Confirm using our mobile app**, never **Sign into our mobile app**. Then sends the push and waits up to 5 minutes for the dashboard |
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

Nothing in this package has run on the real site yet. It was built from a working smoke test,
which signed in, verified the device, and downloaded an activity CSV, unattended and on a locked
session. The sign-in selectors and verification words come from that test.

**Not yet run:**

- Sign-in and verification through this package's code, including clicking **Confirm using our
  mobile app**: its selectors are candidates built from a screenshot of the chooser
- Everything on the statements pages: the row selectors, the download links, the Standard and
  Single choices, how each kind delivers its PDF, and whether the page shows the suffix for the
  `match` check. These are lists of candidates until a `--dump` and `--capture` run shows the
  real pages
- The PDF check against a real statement: which date formats it shows
- Sign-out: the selectors are guesses
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

Fill in `login_url`, and for each account its `statements_url` and `suffix`. Copy each
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
statement a run would save. It clicks nothing on those pages and downloads nothing. It also saves
the sign-in, verification chooser, after-push, and dashboard pages as `dump_site_<step>`. The
sign-in page is saved before anything is typed.

`--capture` opens the job's browser on the sign-in page and saves a snapshot each time the page
changes, for up to 30 minutes or until you close the window. It types nothing and starts no
trace. Use it to record the Standard or Single choices, which `--dump` can't open without starting
a download. It's also the fallback for signing in by hand if a job's verification doesn't go
through.

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

The limit allows 5 minutes of verification and 10 accounts at their slowest. Raise it if you add
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
