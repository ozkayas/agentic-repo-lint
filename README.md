# agentic-repo-lint

A cleanup audit for repos that coding agents work in. After months of agent work a repo collects things nobody notices: skills that are never invoked, MCP servers that are never called, instruction lines about tools that are gone, the same rule kept in two files, plans from finished work that agents still read as current. This skill has your agent find them and write a report.

It is not a security scanner, and it enforces no layout. Every finding says what is there, why it costs you something, and what you could do about it. You decide.

## A procedure, not a linter

The skill is a procedure your agent follows. It says where clutter builds up, what it looks like there and why it matters. There is no bundled script: the agent learns your OS and which agents you use, checks their current documentation when something is unclear, and decides for itself whether a throwaway script is worth writing for your machine.

## How a run goes

1. **Orient:** the OS, the agents in use, and where each one keeps instructions, skills, MCP servers, settings, hooks, memory and logs on this machine.
2. **Inventory:** everything that reaches an agent started in this repo, including the global config it inherits, with what each item costs in context.
3. **Usage evidence:** from your local session logs, how many sessions used each skill and MCP server, and when last.
4. **Judge:** which items are clutter, after opening the file behind each one.
5. **Verify:** a subagent gets the bare claims and tries to refute them. What it refutes is dropped.
6. **Report:** numbered findings, heaviest first, each marked as affecting this repo or every repo.

Nothing is changed during a run. If you then ask for some findings to be applied, the agent does exactly those, with backups.

## What it finds

Six kinds of clutter:

| Kind | Example |
|---|---|
| **unused** | a plugin whose 14 skills are listed in every session and were never invoked |
| **misplaced** | a `SKILL.md` in a folder no agent loads; a skill only one of your agents can see |
| **stale** | instruction lines about a tool you dropped; permission rules for an MCP server that is gone; plan folders from finished work |
| **duplicate** | `AGENTS.md` and `CLAUDE.md` with separate content; one MCP server configured in four files |
| **dead** | a hook whose script is gone; a path in `AGENTS.md` that no longer exists |
| **heavy** | a skill for another stack, loaded into every session of this repo |

The areas it covers: instruction files, memory, skills, MCP servers, settings and permission rules, hooks, subagents and commands, plugins, and what agents left in the repo itself. The full list, with the reason behind each item, is in [`references/where-to-look.md`](skills/agentic-repo-lint/references/where-to-look.md).

## Supported agents

The reference carries starting points for **Claude Code**, **Codex** and **Antigravity**. For any other agent that supports skills, the procedure still applies: the agent looks up that tool's equivalents in its documentation and says in the report what it could not cover.

Usage counts depend on session logs the agent can read. Where there are none, the report says "no evidence" instead of zero.

## Install

```bash
npx skills add ozkayas/agentic-repo-lint -g
```

To pin a release instead of following `main`, add its tag from the [releases page](https://github.com/ozkayas/agentic-repo-lint/releases):

```bash
npx skills add ozkayas/agentic-repo-lint@<tag> -g
```

Then, inside a repo, ask your agent to run it. In Claude Code:

```
/agentic-repo-lint
```

It is user-invoked only (`disable-model-invocation: true`), so it never runs on its own and adds nothing to your context until you call it.

**Requirements:** an agent that supports skills. Verification works best when the agent can start subagents; without them it re-checks its own findings and says so.

## Privacy

Everything runs locally, inside your agent session. The agent reads your repo, your agent config and your session logs. The audit writes nothing outside a temp directory. Secret values are never printed: if a key turns up in a file the agent reads, the report names the file and the key, not the value.

## Changes

See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
