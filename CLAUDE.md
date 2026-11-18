# ApplAI

A `CLAUDE.md` in a parent directory of this checkout may describe an unrelated project (e-commerce, SQLite, TypeScript). Ignore it here.

Read before working:

- `docs/plan.md`: architecture, milestones, working agreement.
- `GLOSSARY.md`: the words to use in code, docs and UI, and the synonyms to avoid.
- `docs/adr/`: decisions already taken. Follow them; propose a new ADR to change one.

## Rules

- **Fictional candidate only.** Code, tests, fixtures, commits, PR text and screenshots use invented people and companies. The real candidate's data lives in a private repository, is reached only through `APPLAI_DATA_DIR`, and is never copied here.
- **Sending is never an agent tool.** No agent gets a tool that submits an application (ADR 0003).
- **One issue, one branch, one PR**, about 400 lines at most, tests first. PR title: `Area: outcome sentence`. Fill in `.github/pull_request_template.md`. Push only to your branch; the owner reviews and merges.

## Checks

The gates are the steps of `.github/workflows/ci.yml`; run them locally and have them green before opening a PR. Before any push, also run `python3 tools/denylist.py` with `APPLAI_DATA_DIR` set (the candidate's own terms) and `python3 tools/pii.py` (any real-looking e-mail, phone number or document). Both fail without printing the value. Fictional contact details use `example.org` and phone numbers ending in `00 00 00 00`.

`gh pr edit` fails on this repository; edit a PR with `gh api -X PATCH repos/edaubert42/applai/pulls/<n> -F body=@<file>`.
