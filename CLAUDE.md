# ApplAI

A `CLAUDE.md` in a parent directory of this checkout may describe an unrelated project (e-commerce, SQLite, TypeScript). Ignore it here.

Read before working:

- `docs/plan.md`: architecture, milestones, working agreement.
- `GLOSSARY.md`: the words to use in code, docs and UI, and the synonyms to avoid.
- `docs/adr/`: decisions already taken. Follow them; propose a new ADR to change one.

## Rules

- **Fictional candidate only.** Code, tests, fixtures, commits, PR text and screenshots use invented people and companies. The real candidate's data lives in a private repository, is reached only through `APPLAI_DATA_DIR`, and is never copied here.
- **Sending is never an agent tool.** No agent gets a tool that submits an application (ADR 0003).
- **One issue, one branch, one PR**, about 400 lines at most, tests first. PR title: `Area: outcome sentence`. Fill in `.github/pull_request_template.md`: at most 150 words, CI checks it. To change a description, rewrite it; never append to it. Push only to your branch; the owner reviews and merges.

## Tests

- One test per acceptance criterion of the issue, written first and seen failing.
- Tests go through the public interface and touch real things: a real SQLite database in a temporary directory, real migrations, the real HTTP app. A test that would still pass with the feature broken proves nothing; mocks are for the network and for model calls only.
- For each call in a change that can fail (HTTP request, model call, validation, a step of a state machine), write a test that makes it fail through the public interface and asserts three things: the failure is recorded where the owner can see it, the state does not move as if it had succeeded, and a retry is safe (nothing is sent or submitted twice).
- Test data is the fictional candidate's.

## Checks

The gates are the steps of `.github/workflows/ci.yml` and, for `frontend/`, of `frontend-ci.yml`; run them locally and have them green before opening a PR. Before any push, also run `python3 tools/denylist.py` with `APPLAI_DATA_DIR` set (the candidate's own terms) and `python3 tools/pii.py` (any real-looking e-mail, phone number or document). Both fail without printing the value. Fictional contact details use `example.org` and phone numbers ending in `00 00 00 00`. Agents running in GitHub Actions have no `APPLAI_DATA_DIR`: they skip the denylist, which CI runs on their PR.

## Agents that build ApplAI

Agents in `.github/workflows/agent-*.yml` build ApplAI from GitHub issues ([ADR 0009](docs/adr/0009-agents-build-applai-from-issues.md)): the coder builds an issue labeled `ready-for-agent` once every issue in its `Blocked by` is closed (the owner may label a whole backlog at once), when the coder stops with questions the answerer answers what the sources settle and starts it again (the owner's reply to the rest starts it too), the reviewer reviews each PR, the fixer answers the owner's `@claude`, repairs red CI on `agent/*` branches, and applies the reviewer's `changes needed`. After each merged PR, the learner reads what went wrong on recent ones and may propose a rule or a check as an issue labeled `lesson`; it only reads, and the owner accepts a lesson by labeling it `ready-for-agent`. Difficulty is measured, not only guessed: after a second failed review the PR becomes `hard` and Opus takes over; after a third, the agents stop and hand over to the owner. Agents push branches and open PRs; only the owner merges.

How agents work on a PR:

- A PR comment is short: outcome first, then at most 5 short lines or a short checklist. No paragraphs. Details go in the commit message or inline comments.
- A review reports only what blocks a merge: at most 5 findings, at most 3 lines per inline comment. Minor points share one non-blocking line. A bug blocks, even a rare one.
- A tool you need is on `main` but not on your branch: `git merge origin/main`, then go on. Never rebase, never force-push.
- A new migration takes the next number after the last one on `main` and writes that number in `src/applai/migrations/HEAD`. A conflict on that file means another PR took the number: merge `origin/main` and take the next one.

An issue is `hard` when any of these holds; otherwise it is normal:

- it touches a rule that protects the candidate: sending, the gates, autonomy levels, fact citation (ADR 0003, 0004, 0008);
- it designs a state machine, concurrency, or a migration that changes existing data;
- it designs an agent's prompt or tools, or changes what the bench measures;
- it touches security: secrets, authentication, the agent workflows;
- no existing pattern in the code shows how to do it.

The owner questions a `hard` issue (numbered questions, a recommended answer each) before labeling it `ready-for-agent`: most review rounds come from an unclear issue, not from the code.

| Agent | Normal | `hard` |
|---|---|---|
| Coder | Sonnet 5.5, effort high | Opus 5.5, effort high |
| Answerer (coder's questions) | Sonnet 5.5, effort high | Opus 5.5, effort high |
| Reviewer | Sonnet 5.5, effort medium | Opus 5.5, effort high |
| Fixer (`@claude`) | Sonnet 5.5, effort high | Opus 5.5, effort high |
| Fixer (red CI) | Sonnet 5.5, effort high | Sonnet 5.5, effort high |
| Fixer (review) | 1st failed review: Sonnet 5.5, high | 2nd: Opus 5.5, high, and the PR becomes `hard` |
| Learner (each merged PR) | Sonnet 5.5, effort high | Sonnet 5.5, effort high |

`gh pr edit` fails on this repository; edit a PR's title or description with `python3 tools/edit_pr.py <n> --title <title> --body-file <file>`. Agents never close or merge a PR: say what blocks you in a PR comment instead.
