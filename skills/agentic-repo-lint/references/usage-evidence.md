# Usage evidence

How to find out whether a skill or an MCP server is still used, from the agents' own session logs. The counts are evidence to weigh beside git history and the repo's stack; they do not decide anything alone.

## What counts as a use

One **session** in which the agent

- invoked the skill (a skill tool call, a `/name` command the user typed, or a read of that skill's `SKILL.md`), or
- called a tool of the MCP server.

Count sessions, not lines. For each skill and server report: sessions per agent, the last date, and whether any of those sessions ran in this repo.

## Where the logs are

Starting points; confirm them on the machine.

- **Claude Code:** `~/.claude/projects/<repo path with dashes>/*.jsonl`, one folder per repo, so "used in this repo" is a folder filter. Each extra config directory has its own `projects/`.
- **Codex:** `~/.codex/sessions/**/*.jsonl`.
- **Other agents:** find where they keep sessions. If they keep none you can read, report "no evidence" for their items; a missing count is not a zero.

## Lines that only look like a use

- **Listings.** Every session starts with the list of available skills and the definitions of every MCP tool. A name appearing there is not a use: count tool *calls* only. In Claude Code logs a call is a `"type":"tool_use"` entry whose `name` is `mcp__<server>__<tool>`; tool definitions carry the same `name` without being calls. In Codex logs an MCP call is an `McpToolCall` item with a `server` field.
- **Subagent logs** belong to the session that started them. Claude Code keeps them under the session's folder, sometimes nested: `<session>/subagents/agent-*.jsonl`, `<session>/subagents/workflows/<run>/agent-*.jsonl`. Count the parent session once.
- **Audit sessions.** A session that reads most skills at once is an audit like this one, not use. Leave it out, and leave out the current session: in Claude Code its id is `${CLAUDE_SESSION_ID}`, which is also the name of its log file.
- **Plugin names.** A plugin's skill is invoked as `<plugin>:<skill>` and its server appears as `plugin_<plugin>_<server>`; match both forms.
- **Time windows.** A zero inside the last 30 days is not "never used". Say which period the count covers.

## Practical notes

- Logs often run to several GB. Scan line by line, test for a cheap substring first, and parse only the lines that pass.
- A sandbox can forbid process pools or reads outside the repo. Fall back to a single process, and when the logs cannot be read at all, say so in the report.
- Pair every count with `git log --follow` on the item's file. A skill that is edited often and never invoked may be one the user is still building.
