# Repository conventions for Claude Code

## Hard rules — never violate

- **Never merge pull requests.** Do not run `gh pr merge`, `git merge` into main,
  or any equivalent. The human maintainer merges manually via the GitHub web UI.
- **Never push directly to `main`.** Always work on a feature branch.
- **Never close PRs** without explicit instruction.
- After opening a PR, the task is done. Do not follow up with a merge.

## Workflow

1. Create a feature branch: `feature/<short-description>`
2. Make changes, commit with a clear message
3. Push the branch
4. Open a PR with `gh pr create` — describe what changed and why
5. Stop. Wait for human review and merge.
