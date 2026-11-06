# New automation

Build a new automation in this repo. The working directory is the `homeLab` repo root.

## My brief

<!-- Fill this in before pasting. Keep real host names, paths, URLs, account details,
     and spreadsheet IDs out of it: describe them, and they'll go in private config. -->

- **Process name:** <neutral name, or "suggest one". Never a bank's name; use `bank1` for a bank I sign in to>
- **What it does:** <one or two sentences>
- **Source:** <web page / SSH / API / file / other, and what kind of system it is>
- **What to capture:** <the fields or files, and an example of what they look like, made up if needed>
- **Sign-in:** <none / what kind, and whether MFA is involved>
- **Where it goes:** <CSV / Google Sheet / webhook message / other>
- **What counts as a repeat:** <what makes two rows the same, or "help me decide">
- **Anything else:** <constraints, or things you know will be awkward>

## Before writing code

1. Read `CLAUDE.md` and `scripts/automation/CLAUDE.md`, and follow them throughout. Then
   read `scripts/automation/README.md` and the closest existing process folder as a
   working example. Read `scripts/automation/agent/README.md` for how jobs get scheduled.
2. Don't read anything under `C:\automation`, or any real config, key, or token. Don't
   run anything against a real site or host unless I ask.
3. Ask me what the brief leaves unclear, then give me a short plan: the modules, what
   you'd reuse from `automation_common`, any new dependencies, and what you can't verify
   from here. Wait for my OK before building.
4. Check whether the source needs something the repo hasn't done yet. If so, tell me the
   options and your recommendation rather than choosing silently, and say whether it
   belongs in this process's package or in `common/`.

## Learn the real source, don't guess

Real data often differs from how I describe it, so base the parser on what the source
really returns. I run anything that touches a real system, so give me the commands, and
I'll paste back the output with private values replaced. Then write a synthetic fixture
that mirrors the real structure with made-up names and values, and build the parser and
its tests from that.

Use whichever of these fits, and apply the same care to any other kind of source:

- **Web:** follow "Browser rules" in `scripts/automation/CLAUDE.md`. With my OK, you may
  load a public page once in the built-in browser. Never sign in to anything yourself.
  Add a `--dump` option so I can capture the page's structure on the test workstation.
- **SSH or another remote system:**
  - Give me a read-only command to capture sample output, and a `--dump` option like the
    web one.
  - Host, user, and paths go in private config. Store the private key in Credential
    Manager as `<process>/ssh-key`, like other short secrets. It must fit, so check the
    limit in `machine-prep.md`.
    Load the key from memory, and never write it to disk, which rules out tools that
    need a key file. A pasted key has several lines and a hidden prompt can't take them,
    so give me a command that reads the key from a file I choose, without printing it.
    I store it myself.
  - Use a key made for this job, not a personal one. Tell me what to restrict it to in
    the server's `authorized_keys`.
  - Verify the server's host key. Never turn that check off or accept unknown hosts
    automatically. Tell me how to record the trusted key.
  - Use a login limited to what this job needs, and only read from the remote system
    unless the brief says otherwise.
  - Set timeouts on connecting and on the command, so a hung connection can't use up the
    job's time limit silently.
- **API or file:** say how it signs in, its limits, and what happens when the data is
  missing or only partly written.

## Decide these explicitly

These have caused real problems here, so state each decision in your plan:

- **What makes a row a repeat.** Base it on the data's identity, never on when it was
  fetched. A timestamp in the comparison caused duplicate sheet rows when two runs
  fetched the same value.
- **Re-running.** A second run the same day, or a run from another machine, must not
  duplicate anything. A run after a failed write must catch up by itself.
- **Downloaded files.** Each run overwrites what's in the data folder, because a re-run
  means I want the freshest copy. Don't compare files, keep old copies, or manage
  staleness. A file must pass its checks before it replaces a good one. Rows appended to
  a sheet or log are different, because a repeat there is a real duplicate.
- **Separate test data.** The test workstation shouldn't write to live destinations, so
  say what the test config should point to.
- **Data that isn't ready.** If the source can return data that isn't ready yet, the run
  must detect that and not save it.
- **Partial failure.** Each stage and item fails on its own and is recorded, and the run
  exits 1. Nothing waits for a person, except an MFA wait with a timeout.

## Scheduling and deployment

Home Assistant is the scheduler, and `automation-agent` starts the job. I set the schedule
myself, so don't ask how often it runs. Don't add a Windows Task Scheduler entry for the
job. When you finish, give me these as steps:

- The `[[jobs]]` block for `C:\automation\agent\config.toml`, and the agent restart it
  needs to pick up the new job. Set `timeout_minutes` above the run's longest possible
  time, with every MFA or other wait added up. The agent stops a job instantly at its
  limit, which leaves no report or notification.
- The Home Assistant automation in YAML, publishing `run` to `automation/<job>/run`, with
  the time left for me to fill in.
- The private setup for this process: config values, secrets, and any Google or Home
  Assistant setup, as things only I can do.

On the robot, code deploys when I merge and press **Update** in Home Assistant, but
config changes and new secrets still need me to sign in. Tell me if this job needs
anything else on the robot, such as a new dependency or a tool outside `uv sync`.

## Testing

- Unit tests use synthetic data only, and nothing touches a network or real system. Run
  `uv run pytest`, `uv run ruff check .`, and `pre-commit` before you say it's ready.
- Give me PowerShell test steps for the robot test workstation, in `powershell` blocks I
  can paste, each with what I should expect to see. When a step runs the tests, say how
  many should pass. That machine has a read-only clone,
  its config uses the `automation-test` topic prefix, and I start its `automation-agent`
  task only while testing. It updates whichever branch is checked out.
- Before you give me a test step, check that it can't undo what it's testing. For example,
  don't move a checkout to a commit that's missing the file being tested.
- Test by running the command directly first, then from the Home Assistant button, then
  on a schedule.
- If the job signs in, plan a run with a new browser profile and no manual sign-in first,
  usually the robot's first run, with me ready to answer any MFA. A profile the site
  already remembers hides the sign-in prompts, so only a new one tests them.
- When something fails, find the cause from the evidence before changing code. If you
  suspect your own test steps or a test fake, prove it.

## Git

Work on a branch named `feature/<process>-<what>`, in small commits. Ask before each push
and before opening a PR, and never merge or tag. Update the process's `CHANGELOG.md`. I
may squash merge, so don't write steps that rely on a branch's commits staying in `main`'s
history.

## When you finish

Tell me what you built, what you tested and how, what you couldn't verify, and what I need
to check. If you changed anything in `common/` or the agent, say what else it affects.
