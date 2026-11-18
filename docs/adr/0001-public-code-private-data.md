# Public code, private data, in two repositories

ApplAI is a public portfolio project, but it runs on a real candidate's profile, CVs, target offers and labels, none of which may be public. The code lives in the public `applai` repository; each candidate's data lives in a private data-only repository (`applai-private`), cloned anywhere on the candidate's machine and read only through `APPLAI_DATA_DIR`. The public repository ships a fictional candidate for tests, screenshots and the demo. A pre-commit hook and CI run gitleaks plus a denylist of personal terms; the denylist itself stays private (local file, CI secret).

## Considered Options

- **Data copied into the public repo, gitignored**: one mistake publishes it forever, and there is no backup or history.
- **The old private repository read in place**: its manual-workflow layout would become the data format, which no other user could adopt.
- **A plain local folder**: private, but no history, no backup, nothing to sync when ApplAI moves to the cloud.
