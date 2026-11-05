# innago-portal

Automations for a property management site. Every command in this package shares one
sign-in, one browser profile, and one config file, so a remembered device carries over.

| Command | What it does |
| --- | --- |
| `innago-invoices` | Downloads each unit's invoice and names it from the PDF's text |

## innago-invoices

Each run signs in, opens the invoice list, switches it to `Not Grouped`, and reads each
row's unit and invoice number. It then opens each invoice's own page directly and clicks
Download. Each file is saved as:

```text
C:\automation\innago\data\invoices\<yyyy-MM>\<due date>_<unit>_<invoice number>-1.pdf
```

- **It takes the period the site shows.** It only chooses a month if the list doesn't
  show one invoice per listed unit. Then it clears the filters and filters to the current
  month by due date alone.
- **The name comes from the PDF.** The site gives each download a random name, which is
  ignored. The `-1` matches what `scripts/innago_mergeInvoices.py` expects. This job
  doesn't merge anything.
- **Running again adds nothing.** A file is the same invoice if its name matches. The
  site builds a new PDF for each download, so bytes can't be compared, but text can.
  Same text is `unchanged`. Different text, such as an invoice that's now paid or late,
  replaces the saved file, and the old copy goes to the run folder under `replaced\`.
- **It only opens invoices and clicks Download.** Remind, Record Payment, Edit Invoice,
  and Delete sit right beside that button, so it must be visible and labeled exactly
  `Download`. It first waits for the page's heading to show the invoice being opened.
- **It needs no PDF settings in Edge.** The site opens each invoice in a new tab, and
  the job keeps a copy of the PDF as it arrives. While it runs, Edge's downloads panel
  may show a file that won't open, which is expected.

### What a run does when something's wrong

| Situation | Result |
| --- | --- |
| The list shows fewer invoices than it has, like `5 of 7` | Every unit fails and nothing is saved |
| A listed unit has no invoice in the view | That unit fails. The others still save |
| A PDF is for a unit that isn't in the config | Not saved. That item fails |
| A PDF's unit doesn't match the unit in its row | Not saved. That item fails |
| A second invoice for the same unit | The first is saved, the second isn't. That item fails |
| A PDF is missing a date, unit, or invoice number | Not saved. That item fails |
| No PDF arrives after a click | That item fails, and the page is saved as evidence |
| Sign-in fails | Every unit fails and nothing is downloaded |

Anything not saved stays in the run folder under `downloads\`. Any failure means exit
code 1 and a `FAIL` notification titled `innago-invoices`. Results are in the newest
`C:\automation\innago\runs\<timestamp>\` folder, in `report.txt` and `run.log`.

## How sign-in works

Each step is worked out from the page, because any of them can be skipped:

| Page | What happens |
| --- | --- |
| Already signed in | Carries on, because the profile is still valid |
| Email, then password | Fills each in once, from `innago/login` |
| Rejected email or password | Fails right away. Never retried, so it can't lock the account |
| Passkey prompt, like "Log In Faster on This Device" | Clicks `Not on this device`, and fails if there's no way to decline. Never creates one |
| MFA | Waits 1 minute for someone to finish it in the window, then fails. The notification says `FAIL: MFA needed` |
| CAPTCHA or bot check | Fails right away. Never worked around |
| Anything else | Fails after 30 seconds on the same page |

Nobody can type a code on the robot's locked screen, so an MFA prompt there fails after
the wait. Run `--capture` to sign in by hand, after which the profile may be remembered.

A normal run takes about 2 minutes. The agent's limit stops a job instantly, and a job stopped
that way saves no report or evidence. So keep the limit well above the MFA wait.

A passkey dialog from Windows itself, rather than from the page, can't be dismissed from
here. It would use up the sign-in's time and fail with a screenshot.

The email, password, invoice list, and invoice page steps have all run on the real site.
The site hasn't shown a passkey prompt or MFA to the job yet, so those two steps come from
how this kind of sign-in usually works, and have only run against a test site.

## Set up each machine

Do these signed in as the Windows user that runs the job, in
`C:\src\homeLab\scripts\automation`.

**1. Store the sign-in.** The prompt hides the password:

```powershell
uv run python -c "import getpass, keyring; keyring.set_password('innago/login', input('Email: '), getpass.getpass('Password: '))"
```

**2. Create the config:**

```powershell
New-Item -ItemType Directory C:\automation\innago -Force | Out-Null
Copy-Item innago\config.example.toml C:\automation\innago\config.toml
notepad C:\automation\innago\config.toml
```

List every unit that must have an invoice, as it appears after `Unit` on the invoice.

## Run

```powershell
uv run innago-invoices --capture   # by hand: you sign in and click, page structure is saved
uv run innago-invoices --dump      # signs in and reports names; saves nothing under data
uv run innago-invoices             # normal run
```

`--capture` and `--dump` save page outlines, screenshots, and for `--dump` the page's
HTML to the run folder. These hold real names and addresses. Replace private values
before sharing any of them, and never commit them.

## Schedule (robot only)

Home Assistant starts it through [automation-agent](../agent/README.md). For now it's
run from the **Automation robot** device's `innago-invoices run` button, with no
schedule. Add this to `C:\automation\agent\config.toml` and restart the agent:

```toml
[[jobs]]
name = "innago-invoices"
command = "innago-invoices"
timeout_minutes = 5
```

## Add another command

1. Add a module with its own `main()`, and a line under `[project.scripts]` in
   `pyproject.toml`.
2. Get a signed-in page from `innago_portal.session.signed_in_page`. It handles the
   profile, credential, trace, and failure evidence. Don't sign in any other way.
3. Pass `title="<command>"` to `Run.start`, so its notifications name the command.
4. Add its settings as a new section in `config.py`. `[site]` is already shared.
