# Repository conventions for Claude Code

Global rules for the whole repo. Work under `scripts/automation/` also follows
[`scripts/automation/CLAUDE.md`](scripts/automation/CLAUDE.md).

## Hard rules — never violate

- **Never merge pull requests.** Do not run `gh pr merge`, `git merge` into main,
  or any equivalent. The human maintainer merges manually via the GitHub web UI.
- **Never push directly to `main`.** Always work on a feature branch.
- **Never close PRs** without explicit instruction.
- **Never create tags.** The maintainer creates them.
- **Ask before** pushing a branch, opening a PR, or doing anything the task
  didn't list.
- After opening a PR, the task is done. Do not follow up with a merge.
- **Never include local machine hostnames** in anything pushed to GitHub
  (commit messages, PR text, file contents, or commit author/committer emails).
  Before committing, confirm `git config user.email` is set to the GitHub noreply
  address, not a `user@<hostname>.local`-style default. This repo is public.

## Language and formats

- Write US English everywhere (code, comments, docs, commits, PRs, chat)
  unless explicitly told otherwise: "behavior", "labeled", "color",
  "organize", "canceled", "center", "license" (noun and verb).
- Use the Oxford comma in lists of three or more: "code, tests, and docs".
- Numbers use a period as the decimal separator (`49.275`, `18.90`) unless
  explicitly told otherwise. In data files, write no thousands separators and
  no currency symbols.

## Working with the maintainer

- When you have several questions, print the full list first, then ask them one
  at a time in a logical order, starting with the one that shapes the most
  decisions. Wait for each answer before asking the next.
- End every command block meant for pasting with an extra blank line inside the
  code fence, so the last command runs without another Enter.

## The repo is public

- Never commit, print, or log real values: account numbers, credentials,
  private URLs, host names, email addresses, spreadsheet IDs, webhook URLs.
  Use `<placeholders>` in docs and `config.example.toml` files.
- Never name any bank anywhere: code, comments, tests, file names, commits,
  issues, PRs. Test sites are called **FakeBank**.
- Test data is synthetic. Never commit real downloads, screenshots, page
  dumps, or traces.
- Never read real config or secrets: `config.toml`, `.env`, keys, tokens,
  and anything else private kept outside the repo.

## Workflow

1. Create a feature branch: `feature/<short-description>`.
2. Work in small commits with clear messages. `pre-commit` must pass; never
   use `--no-verify`.
3. Ask, then push the branch.
4. Ask, then open a PR into `main` with `gh pr create`: what changed, why,
   and what the maintainer must check by hand.
5. Stop. Wait for human review and merge.
