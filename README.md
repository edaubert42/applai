# ApplAI

Claude agents that prepare job applications, while the candidate decides at every gate and is the only one who sends.

| Stage | Who | What happens |
|---|---|---|
| Watch | code | new offers from Google, LinkedIn, Notion and e-mail alerts |
| Triage | Claude Haiku | a score, its reasons and its doubts |
| **Gate** | candidate | accept or decline the offer, with a reason |
| Drafting | Claude Opus | answers, cover letter and tailored CV; every sentence cites a verified fact |
| **Gate** | candidate | accept, edit or reject each answer |
| Filling | Claude Sonnet + Chrome extension | the form filled in the candidate's own browser |
| Review | Claude Sonnet, fresh context | the problems it finds on the filled form |
| **Gate** | candidate | final validation, then the candidate sends |

What it shows:

- **Guardrails by design.** No agent has a tool that sends an application ([ADR 0003](docs/adr/0003-sending-is-a-human-decision.md)). Every decision at a gate is recorded, so a gate can later pass on its own once the candidate has stopped correcting it ([ADR 0004](docs/adr/0004-autonomy-is-earned-per-level.md)).
- **Everything is measured.** Triage and Drafting are scored on a fixed evaluation bench, and every agent run records its model, tokens, duration and estimated cost in the application's trace.
- **Built by agents too.** Of the 130 pull requests merged from 18 to 26 November 2026, 78 were opened by a coder agent working from a GitHub issue, then checked by a reviewer agent; I review and merge every one.

**Status:** every stage above is built. Next: a public demo with a fictional candidate, and the Control screen that measures each gate.

## How it's built

I build ApplAI with Claude Code, the way ApplAI works: agents do the work, a human decides.

- **I decide** goals, scope and design. Each choice and its reason is a [design decision](docs/adr/).
- **Agents build the issues** in GitHub Actions: a coder opens the pull request, a reviewer reads it with a fresh context, a fixer repairs failing checks, a learner proposes a rule when a mistake comes back ([ADR 0009](docs/adr/0009-agents-build-applai-from-issues.md)). Hard issues go to Opus, the rest to Sonnet.
- **I merge** only once lint, types, tests and a scan for personal data pass.

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

`uv run applai demo` serves the public demo at http://127.0.0.1:8000: the fictional candidate of `demo/candidate`, agents replayed from their recordings, nothing sent, and a new database at each start. It needs no setting and no `claude`.

The frontend is in `frontend/` (Node 24): `npm ci && npm run lint && npm run typecheck && npm test && npm run build`, or `npm run dev` while working on it. Its design is in [docs/design.md](docs/design.md).

A denylist check keeps the candidate's personal terms out of the repository. It reads them from `$APPLAI_DATA_DIR/denylist.txt` locally and from the `APPLAI_DENYLIST` secret in CI, and fails without printing them: `python3 tools/denylist.py`. A second check, `python3 tools/pii.py`, fails on any real-looking e-mail or phone number and on documents or images not listed in `tools/allowed_files.txt`. Optional pre-commit hooks: `pre-commit install`.

## License

[AGPL-3.0](LICENSE)
