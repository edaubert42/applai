# Agents build ApplAI from issues; the owner writes the issues and merges

ApplAI is built the way it works: agents do the work, a human makes the decisions. The owner and Claude turn a part of the plan into GitHub issues, each with acceptance criteria and its blocking issues. Labeling an issue `ready-for-agent` starts a coder agent in GitHub Actions (`anthropics/claude-code-action`, on the owner's Claude subscription), which writes the tests first, then the code, and opens a PR. A reviewer agent with a fresh context reviews every PR against its issue. A fixer agent answers the owner's `@claude` comments and makes one attempt to repair a red CI on an agent branch. The owner merges; `main` is protected and only accepts PRs whose checks pass.

Issues stay on GitHub rather than a private tracker so the work is visible next to the code. The model is set per task, never left to the default: Opus for issues labeled `hard` (state machines, fact citation) and their reviews, Sonnet for the rest.

## Considered options

- **Sessions started by hand, one per issue, on the owner's machine** (`claude --bg --worktree`). Kept for issues that need the private data directory, which agents in GitHub Actions never see.
- **One integration branch per milestone part, merged as one PR.** Rejected: one large PR per part breaks the one-issue-one-PR rule and the readable history.

## Consequences

- CI is what lets agent work be trusted without reading every line, so every rule that matters is a check, not only a sentence in `CLAUDE.md`.
- Agent runs count against the owner's subscription limits; few issues are labeled at once.
- Workflows that hold secrets and write access are themselves checked by actionlint and zizmor, with actions pinned to a commit.
