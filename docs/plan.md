# ApplAI: build plan

ApplAI prepares job applications with a pipeline of agents and keeps a human in control. A candidate labels the offers the agents propose, corrects the answers and cover letters they draft, checks the filled form, and decides when an application is sent. Every one of those decisions is recorded, scored, and used to improve the agents, and the dashboards show where each application and each agent stands.

It is built first for its author's own job search, and as a portfolio project: clean history, readable pull requests, real measured numbers. Multi-user and cloud hosting come later, and the design leaves room for them from day one.

Vocabulary: [GLOSSARY.md](../GLOSSARY.md). Decisions and their reasons: [docs/adr/](adr/).

## Principles

- **The human stays in control, and control is measured.** Four control levels (offer, answers, form, full application), each with its own gate. Autonomy is earned level by level from the correction rate, never assumed ([ADR 0003](adr/0003-sending-is-a-human-decision.md), [ADR 0004](adr/0004-autonomy-is-earned-per-level.md)).
- **Code enforces the rules, prompts do not.** No agent has a tool that sends an application.
- **Agents only state facts from the candidate's fact sheet.** Every sentence cites the fact it uses ([ADR 0008](adr/0008-agents-only-write-facts-from-the-fact-sheet.md)).
- **No personal data in this repository.** Code is public, the candidate's data lives in a separate private repository ([ADR 0001](adr/0001-public-code-private-data.md)).
- **Every decision is a label.** Labels feed the dashboards, the evaluation bench and the rules proposed to the agents.

## Architecture

| Part | Choice |
|---|---|
| Repositories | `applai` (public): code, a fictional demo candidate, the public bench. `applai-private` (private): profile, CVs, offers, private bench cases, label exports. Cloned anywhere on the candidate's machine and read only through `APPLAI_DATA_DIR`. |
| Backend | Python 3.12, FastAPI, SQLite with SQLAlchemy and Alembic. Every table carries a `user_id`. |
| Frontend | React, TypeScript, Vite, Tailwind. Live updates over server-sent events. UI in French, English available as a setting; all strings in translation files. |
| Agents | One interface, `run_agent(spec) -> result + usage`, three backends: `ClaudeCLI` (`claude -p`, now), `AnthropicAPI` (later), `ReplayBackend` (demo) ([ADR 0002](adr/0002-agents-run-through-the-claude-cli.md)). Each agent gets only its own tools, served by ApplAI's MCP server. |
| Browser | A `BrowserDriver` interface. ApplAI Chrome extension in the candidate's own Chrome for real applications; Playwright for the bench and CI ([ADR 0005](adr/0005-browser-layer-is-a-chrome-extension.md)). Milestone 1 uses Playwright on a dedicated profile until the extension lands. |
| Runs | Locally for now. Cloud and multi-user later, with the extension as each user's local browser agent. |

### Pipeline

| Stage | Model | Produces |
|---|---|---|
| Watch | code (plus agents for e-mail alerts) | new offers from career pages, Notion, LinkedIn, e-mail alerts |
| Triage | Haiku | score, reasons, doubts |
| **Gate: offer** | candidate | accept / decline with a reason |
| Form capture | script, Sonnet as fallback | the form's fields as JSON |
| Drafting | Opus | answers and cover letter, each sentence citing a fact |
| Filling | Sonnet + browser driver | filled form and a screenshot, no access to submit |
| Review | Sonnet, fresh context | list of problems |
| **Gate: answers, form, final validation** | candidate | accept / edit / reject each answer, fix fields, then validate |
| Send | candidate, or ApplAI on a per-application authorization | sent application, detected on the confirmation page |

Each call records tokens, estimated cost and duration in the application's trace. `claude -p --output-format json` already reports `total_cost_usd` and per-model usage at API list price; agents use `--system-prompt` so the estimate is not inflated by the CLI's default system prompt.

## Control and autonomy

- Each level's gate records a label: offer (accept, decline, later + reason), answer and letter (accept, edit with word-level diff + reason, reject and regenerate + reason), form (fields corrected), full application (validated, sent).
- A level unlocks when its correction rate is at most 10 % over a sliding window **and** at least 20 applications have been checked at that level. It relocks on any invented fact or wrong field.
- Final validation stays in ApplAI. After it, the candidate either opens the form and sends it themselves (required with a captcha or a consent box) or authorizes ApplAI to send this one application. Automatic sending is allowed only once the full-application level is unlocked.
- Thresholds are settings.

## Data

- **Profile** (`profile.yaml`): identity, base answers (FR/EN), targets (roles, contract priority, locations, salary), validated sentences, forbidden facts, and **facts** `#F1…`.
- **Experience**: as much detail as possible per job (context, missions, stack, numbers with their source, results, STAR stories, motivation, angles per target role), stored as facts.
- **Fact** fields: `status` (validated, to confirm, forbidden) and `diffusion` (free, applications only, never). Usage is tracked: which answers used a fact, and how often they were accepted.
- **Candidate rules become settings**: grammatical gender of job titles, which CV to send (language, contract type, photo by company type), typography, per-company application limits, cover-letter policy.

## Milestones

Milestones are defined by content, not dates.

### Milestone 1: the core loop, used for real

- **Part 1**: skeleton and CI, data directory and importer, database and orchestrator, `run_agent` + trace, Triage agent and its bench, the **To review** screen (offer labeling), the **Applications** dashboard (sent, waiting, in progress, including those still handled by the old workflow).
- **Part 2**: Drafting, the **Correct** screen (answers and cover letter), filling for Ashby and Lever (Playwright, dedicated profile), final validation with both send buttons, the **Control** dashboard per level, a simple live **Flow** view, all watch sources (career pages, Notion, LinkedIn, e-mail alerts).
- **Part 3**: the ApplAI Chrome extension, the generic path for any ATS, LinkedIn Easy Apply (a few per day, human-paced, stops at any verification), the full Flow graph and timeline.
- **Exit**: the author applies through ApplAI. ApplAI goes on the CV with measured facts only. README with a demo GIF and the bench's version table. Public demo on Railway ([ADR 0007](adr/0007-public-demo-replays-recorded-traces.md)).

### Next

1. **Interview prep**: for each application that reaches an interview, a prep sheet (the offer, the company, what the candidate wrote in the application), STAR stories picked from the facts, likely questions, a mock interview with Opus and feedback. Answers can be dictated by voice. New facts found while practicing go back to the profile.
2. **Onboarding**: CV upload as the main entry, LinkedIn data export as an option, Google or Microsoft sign-in for login and mailbox, then an interview per experience until each one has enough facts, then fact validation. The author is the first user: the importer prefills, the interview fills the gaps.
3. **Learning loops**: rules proposed from labels ("stop proposing fixed-term contracts"), applied only after a bench run with no regression. Blind A/B of two answer versions, review time, offers kept for later that end up sent.
4. **Friends trial**: friends install ApplAI on their own machine, with their own private data repository and their own Claude subscription. Needs a one-command install and the onboarding, so onboarding moves ahead of interview prep when the trial starts.
5. **Multi-user and cloud**: accounts, Postgres, the `AnthropicAPI` backend, per-user OAuth for mail, "try it with your CV" in the demo. A hosted version pays for model calls, so it needs a paid offer first (for example, prepaid API credits).

## Evaluation bench

| Stage | Metric | Threshold |
|---|---|---|
| Triage | recall on accepted offers, precision | recall ≥ 90 % |
| Form capture | F1 on fields, type and required flag | ≥ 0.95 |
| Drafting | sentences without a valid fact, or with a forbidden one | 0 % |
| Drafting | judge score, checked against human labels | κ ≥ 0.6 |
| Filling | fields equal to the expected state | ≥ 97 % |
| Filling | accidental sends, including under injected instructions | 0 |
| Review | seeded errors found | ≥ 85 % |
| End to end | fields the candidate changes before sending | ≤ 1 |
| End to end | cost and duration per application | tracked |

- **Public bench** (CI): fictional candidate, about 20 local fake forms covering common traps (multi-step forms, conditional fields, uploads, word limits, decoy submit buttons, hidden instructions in the offer), prompt-injection tests.
- **Private bench** (local only): real offers labeled by the author, real forms, validated answers. Only aggregate numbers are published.
- Triage negatives must include offers the author read and declined, not only those removed by simple rules, or recall will look better than it is.
- Each case runs 3 times. Version v1 is a single Opus agent doing everything: the baseline to beat.

## Transition from the old workflow

- Notion stays the shared ledger while ApplAI and the old workflow run side by side. ApplAI reads rows created elsewhere and writes after each of its sends. One key per application: the offer's official URL. An offer handled by the old workflow shows as such in ApplAI ([ADR 0006](adr/0006-notion-is-the-shared-ledger-during-transition.md)).
- When ApplAI covers every ATS in use, it becomes the source of truth, the Notion sync becomes one-way, then stops.
- Old scripts are ported one by one as tested modules; the old version of a stage is frozen once ApplAI covers it.

## Working agreement

- One issue, one branch, one small PR (about 400 lines at most), squash merge. PR template: what, choices to confirm, how to test, bench impact; 150 words at most, checked by CI.
- CI required from PR 1: ruff, pyright, pytest, gitleaks, and a denylist check (patterns kept in the private repo locally and in a CI secret).
- No push to `main`. PR descriptions, commits and screenshots use the fictional candidate only.
- Each frontend PR carries a short explanation of the React concepts it introduces.

## First pull requests

1. Skeleton: `pyproject`, ruff, pyright, pytest, CI, gitleaks + denylist hook, repo `CLAUDE.md`, short README.
2. Data directory, `profile.yaml` schema, importer from the old repository into `applai-private`.
3. Database models, Alembic, orchestrator state machine.
4. `run_agent`, the `ClaudeCLI` backend, the trace.
5. Triage agent and the triage bench.
6. FastAPI, React skeleton, the To review screen.
7. Applications dashboard and Notion sync.

Then: Drafting and Correct, Filling, final validation, Control, Flow.
