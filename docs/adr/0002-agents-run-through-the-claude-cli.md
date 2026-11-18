# Agents run through the Claude CLI first, behind one interface

Agents call `claude -p` on the author's Claude subscription instead of the Anthropic API, so building and using ApplAI costs nothing per token during the job search. Every agent goes through `run_agent(spec) -> result + usage`; the `ClaudeCLI` backend passes `--tools ""`, `--system-prompt`, `--json-schema`, `--model` and `--output-format json`, and gets tools only from ApplAI's own MCP server through `--strict-mcp-config`. The CLI's JSON output reports cost at API list price, so cost per application is measured from day one. An `AnthropicAPI` backend replaces it for multi-user, and a `ReplayBackend` serves the demo.

## Consequences

- Cost estimates are only comparable to the API if each agent replaces the CLI's default system prompt.
- Subscription rate limits apply; the bench must stay small enough to run on them.
- Connectors reached through the CLI (mailbox, Notion) are tied to the author's account and need their own OAuth before multi-user.
