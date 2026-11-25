# ApplAI

ApplAI prepares job applications with a pipeline of agents while the candidate keeps control at every level. This glossary fixes the words used in code, docs and UI (French UI labels in parentheses).

## People and things applied to

**Candidate**:
The person whose applications ApplAI prepares. Owns a profile and makes every decision at the gates.
_Avoid_: User (except for accounts and authentication), applicant

**Offer** (« Offre »):
A job posting found by the watch, identified by its official URL.
_Avoid_: Job, listing, posting, opportunity

**Application** (« Candidature »):
Everything ApplAI prepares for one candidate on one offer, from the moment the offer is accepted until it is sent or dropped.
_Avoid_: Submission, dossier, file

**ATS**:
The hiring platform that hosts an offer's application form (Ashby, Lever, Workday…).
_Avoid_: Job board, portal

**Form**:
The application form of an offer on its ATS: its fields, their types, which are required, their options.
_Avoid_: Questionnaire, page

## The pipeline

**Stage**:
One step an application goes through: watch, triage, form capture, drafting, filling, review, send.
_Avoid_: Step, phase, task

**Watch** (« Veille »):
The stage that finds new offers in the candidate's sources.
_Avoid_: Scraping, crawl, monitoring

**Source**:
A place the watch reads offers from: a career page, a mailbox of job alerts, a job network, a tracker.
_Avoid_: Feed, channel

**Triage** (« Tri »):
The stage that scores an offer against the profile and gives its reasons and doubts.
_Avoid_: Sorting, filtering, matching

**Form capture** (« Relevé »):
The stage that reads a form and lists its fields without filling anything.
_Avoid_: Scan, parse, extraction

**Drafting** (« Rédaction »):
The stage that writes the answers to open questions and the cover letter.
_Avoid_: Generation, writing

**CV tailoring** (« Adaptation du CV »):
The part of drafting where an agent adapts a copy of the CV to the offer (title line, photo, bullets reordered, reworded or dropped), each change checked by code.
_Avoid_: CV generation, CV customization, resume tailoring

**Filling** (« Remplissage »):
The stage that enters the application into the form, without sending it.
_Avoid_: Autofill, submission

**Review** (« Relecture »):
The stage where an agent checks the filled form against the offer and the profile and lists problems.
_Avoid_: QA, check, verification (reserved for the candidate's part)

**Trace**:
The record of every agent run on an application: model, duration, tokens, estimated cost.
_Avoid_: Log, history

## Control

**Level** (« Niveau »):
One of the four things the candidate controls: the offer, the answers, the form, the full application.
_Avoid_: Tier, layer, step

**Gate**:
The point where a level waits for the candidate's decision.
_Avoid_: Checkpoint, approval, validation step

**Label**:
A decision the candidate records at a gate, with its optional reason.
_Avoid_: Annotation, feedback, rating

**Correction**:
A label that changes or rejects what an agent produced (a declined offer, an edited or rejected answer, a fixed field).
_Avoid_: Fix, edit (as a noun), error

**Correction rate**:
The share of labels at a level that are corrections, over a sliding window.
_Avoid_: Error rate, accuracy

**Autonomy**:
The state of a level whose gate passes on its own because its correction rate is low enough.
_Avoid_: Automation, auto mode

**Final validation** (« Validation finale »):
The candidate's decision that a full application is ready to send.
_Avoid_: Approval, sign-off

**Send authorization**:
The candidate's permission for ApplAI to send one specific application after final validation.
_Avoid_: Auto-submit, consent

**Autosend**:
Sending without a send authorization, allowed only once the full-application level has autonomy.
_Avoid_: Auto-apply

## The candidate's data

**Profile** (« Profil »):
Everything ApplAI knows about the candidate: identity, base answers, targets, experiences, facts, rules.
_Avoid_: Account, CV, resume

**Experience**:
One job, internship or project in the candidate's past, detailed enough to answer questions about it.
_Avoid_: Position, role, job (for past work)

**Fact** (« Fait », `#F12`):
One numbered statement about the candidate that agents are allowed to write. Every drafted sentence cites one.
_Avoid_: Claim, data point, info

**Fact status**:
Whether a fact is validated, still to confirm, or forbidden.
_Avoid_: State, approval

**Diffusion**:
Where a fact may appear: anywhere, in applications only, or never.
_Avoid_: Visibility, privacy level

**Validated sentence** (« Phrase validée »):
A sentence in the candidate's own words, approved for reuse as is.
_Avoid_: Template, snippet

**Target**:
What the candidate is looking for: roles, contract types in priority order, locations, salary.
_Avoid_: Preferences, criteria, goals

## Evaluation

**Bench** (« Banc »):
The fixed set of cases every agent version is measured on.
_Avoid_: Test suite, evals, benchmark

**Bench case**:
One input with its expected result: an offer with its label, a fake form with its filled state, an attack with its expected refusal.
_Avoid_: Fixture, sample

**Version**:
One configuration of the pipeline (prompts, models, split into agents), measured on the bench as a whole.
_Avoid_: Release, build

**Replay** (« Rejeu »):
Running the app on recorded traces instead of live models, as in the public demo.
_Avoid_: Mock, simulation
