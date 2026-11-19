# ApplAI

Agents that prepare job applications while the candidate stays in control.

ApplAI finds offers, drafts answers and cover letters from a verified fact sheet, and fills application forms. The candidate checks every step, and every check is recorded and measured.

- **The candidate stays in control.** Offers, answers, forms and the final application each go through the candidate. No agent has a tool that sends an application.
- **Autonomy is earned, not assumed.** A step runs on its own only once the candidate has stopped correcting it, and it locks again at the first mistake.
- **Everything is measured.** Each agent is scored on an evaluation bench, and cost, time and corrections are tracked for every application.

**Status:** design phase. The code lands through small pull requests.

## How it's built

I build ApplAI with Claude Code, the same way ApplAI works: the agents do the work, a human makes the decisions.

- **I decide.** Goals, scope and design choices are mine. Claude Code lays out the options and their trade-offs; what I chose, and why, is in the [design decisions](docs/adr/).
- **Claude Code writes most of the code**, one small pull request at a time. Most issues are built by agents in GitHub Actions: a coder opens the pull request, a reviewer reads it with a fresh context, a fixer repairs failing checks, a learner proposes a rule or a check when a mistake comes back ([ADR 0009](docs/adr/0009-agents-build-applai-from-issues.md)). That is why commits carry a `Co-authored-by: Claude` line.
- **I review and merge every pull request**, and only once the checks pass: lint, types, tests, and a scan for personal data.
- **Measurements decide whether a change helped.** Every agent is scored on a fixed evaluation bench, and the numbers published here come from it.

## Read more

- [The plan](docs/plan.md): architecture, control levels, milestones, evaluation bench.
- [Glossary](GLOSSARY.md): the words ApplAI uses, and the ones it avoids.
- [Design decisions](docs/adr/): why it is built this way, one short record per decision.

## Development

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

ApplAI refuses to open the candidate's database without a place for its backups: set `APPLAI_BACKUP_DIR` to a directory outside `APPLAI_DATA_DIR`. A copy is made when the newest is over 24 hours old, the 14 latest are kept, and `python -m applai.backup` makes one now.

A denylist check keeps the candidate's personal terms out of the repository. It reads them from `$APPLAI_DATA_DIR/denylist.txt` locally and from the `APPLAI_DENYLIST` secret in CI, and fails without printing them: `python3 tools/denylist.py`. A second check, `python3 tools/pii.py`, fails on any real-looking e-mail or phone number and on documents or images not listed in `tools/allowed_files.txt`. Optional pre-commit hooks: `pre-commit install`.

## License

[AGPL-3.0](LICENSE)
