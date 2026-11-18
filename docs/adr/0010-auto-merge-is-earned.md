# Auto-merge of agent pull requests is earned, like the candidate's autonomy

Merging every agent pull request by hand makes the owner the bottleneck, but merging them unread is only safe once the reviewer agent is known to judge like the owner. So auto-merge follows the rule ApplAI applies to its own agents (ADR 0004). For the first ten agent pull requests the owner merges by hand, and each reviewer verdict is compared with the owner's decision. If they agree on at least nine, auto-merge opens for pull requests that are small and safe: a normal issue, `ready to merge` at the first review, green CI, at most 150 changed lines, and no change to workflows, security tools, `CLAUDE.md`, ADRs or database migrations. The owner reads a daily digest of what merged. The first auto-merged pull request the owner has to fix or revert turns auto-merge off again.

## Considered options

- **Auto-merge from the start.** Rejected: the reviewer's verdicts had never been checked against the owner's.
- **Never auto-merge.** Rejected: once the reviewer is measured, reading every small pull request costs the owner's time and adds nothing.

## Consequences

- The README's "I review and merge every pull request" changes, with the measured agreement, when auto-merge opens.
- The owner must still be able to explain the code; the digest exists for that.
