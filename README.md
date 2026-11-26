# agentic-repo-lint

**Built for Claude Code, Codex and Antigravity.**

A cleanup audit for repos that coding agents work in. Run it every few weeks to find what Claude Code, Codex and Antigravity leave behind: skills nobody uses, MCP servers that never get called, keys pasted into the wrong file, permission rules for tools that are gone, and config that no longer parses.

It is not a security scanner. It looks for agent clutter and the plain mistakes that come with it, then fixes only what you approve.

## A skill, not a script

The skill is procedural memory for your agent: it tells the agent what a well-kept agent setup looks like, why each rule exists, and how to audit against it. Two small Python scripts are its instruments. `lint.py` finds where the setup drifts from the layout; `usage.py` counts how often each skill and MCP server was really used. The agent treats their output as leads, opens the files, judges each lead by the intent of its rule, and reports only what holds up. The judgement calls stay with the agent and with you: is this skill still needed, is this instruction stale, does this server belong here.

## What it finds

| Area | Examples |
|---|---|
| Skills | skills no agent loads, dead paths inside a skill, duplicates between the repo and `~/.agents/skills`, global skills missing a `~/.claude/skills` link, descriptions Claude truncates, how many sessions used each skill |
| MCP servers | servers with zero calls in your session logs, the same server configured for several agents and accounts, hardcoded SDK version paths, destructive tools without a deny rule, plugin-provided servers |
| Keys in the wrong place | a key inside a permission rule, a settings `env` block, a hook command or an MCP config, reported by name and never printed |
| Stale config | permission rules for servers or paths that no longer exist, `skillOverrides` and `enabledMcpjsonServers` entries for things that were removed, broken JSON, hooks whose script is gone |
| Instructions | `CLAUDE.md` and `AGENTS.md` drifting apart, dead paths in instruction files, `@imports` that point nowhere, an empty or oversized `MEMORY.md` |
| Accounts | extra Claude config dirs (`~/.claude-*`) whose skills, `CLAUDE.md` or settings drifted from the main one |

The script finds the mechanical problems; the skill then has the agent judge the rest with you: which skills are still needed, which instruction lines are stale, which servers this repo never uses.

## Supported agents

| Agent | What the scripts read |
|---|---|
| Claude Code | `CLAUDE.md`, `.claude/` (settings, skills, rules, agents, commands), `~/.claude*` (every account), `~/.claude.json`, plugins, session logs |
| Codex | `AGENTS.md`, `.agents/skills`, `~/.codex/config.toml`, `~/.codex/hooks.json`, session logs |
| Antigravity | `AGENTS.md`, `.agents/skills`, `.agents/rules`, `~/.gemini/config` (MCP servers, hooks) |

Other agents (Cursor, Copilot, Windsurf…) are outside the scripts: the skill still runs if your agent supports skills, but its report covers only the three above, and the agent says so. Usage counts come from Claude Code and Codex logs; Antigravity sessions are not counted.

**Platforms:** developed and tested on macOS; Linux should work. On Windows the scripts run, but the suggested fix commands are written for a POSIX shell, so the agent translates them for you.

## Install

```bash
npx skills add ozkayas/agentic-repo-lint -g -a claude-code -a codex -a antigravity
```

Without `-a`, the `skills` CLI asks which agents to install it for.

Then, inside a repo, ask your agent to run it. In Claude Code:

```
/agentic-repo-lint
```

It is user-invoked only (`disable-model-invocation: true`), so it never runs on its own.

**Requirements:** Python 3.9+ and git. Reading Codex's `config.toml` needs Python 3.11+ (`tomllib`); on older versions that one check is skipped with a note.

## How a run goes

1. **Mechanical check:** `scripts/lint.py` reads the repo and your agent config and prints findings with stable ids.
2. **Skill cleanup:** `scripts/usage.py` counts, from your local session logs, how many sessions used each skill and called each MCP server. The agent then gives every skill a verdict (keep, fix, move, delete), one at a time, and waits for your answer.
3. **Judgement review:** the agent reads instruction files, MCP servers and hooks for things a script cannot judge.
4. **Report:** numbered findings (`A-1`, `A-2`…), marked new or persisting since the last run.
5. **Fixes:** only the items you approve. It backs up settings before rewriting them and removes `npx skills` installs with `npx skills remove`. Findings you want to keep are recorded with `--accept <id> --reason '…'`, and later runs stop raising them.

## Example

From a demo repo (`python3 scripts/lint.py --repo acme-app`):

```
skills listed to Claude: 2 (≈10 tokens)  connectors: on  claude.ai skill sync: on
  mcp postgres       .mcp.json                  enabled
  mcp docs           ~/.claude.json             enabled
  mcp docs           ~/.gemini/config/mcp_config.json enabled
NEW 32d55362 [error] perm-secret .claude/settings.local.json: env assignment inside a permission rule: `Bash(PGPASSWORD=<redacted:env assignment> psql -h db.local)`
NEW 7e2dc1ad [warn] doc-refs AGENTS.md: refers to `docs/setup.md`, which does not exist
NEW abfac259 [warn] link-global-skills ~/.claude/skills/pdf-tools: global skill has no link in ~/.claude/skills, so Claude does not see it
NEW fd97e7b5 [warn] perm-risky .claude/settings.local.json: `Bash(npx:*)`: runs any code unasked
NEW bb0722fe [warn] perm-secret ~/.claude.json:docs: secret written inline in the MCP config (headers.DOCS_API_KEY)
NEW 4f3acf38 [warn] perm-secret ~/.claude/settings.json: env `OPENAI_API_KEY` holds a secret value
NEW 354ed15e [warn] skill-dead-ref .agents/skills/release-notes/SKILL.md: names 1 path(s) that do not exist: `scripts/changelog.py`
NEW a9a09636 [warn] skill-misplaced tools/skills: 1 skill(s) no agent loads: old-migration
NEW 6f121a77 [info] mcp-duplicate docs: configured in 2 places: ~/.claude.json, ~/.gemini/config/mcp_config.json
NEW b152ef2d [info] perm-stale .claude/settings.local.json: `mcp__linear__create_issue`: MCP server `linear` is not configured
NEW 70feb9e9 [info] stale-name .claude/settings.local.json: `skillOverrides` names `deleted-skill`, which no longer exists
NEW eebc9251 [info] stale-name .claude/settings.local.json: `enabledMcpjsonServers` names `stripe`, which is not in .mcp.json
```

## Configuration

Out of the box it makes no assumptions about your personal choices. To enforce a stricter layout, create `~/.agents/agentic-repo-lint.json`:

```json
{
  "agents": ["claude", "codex", "antigravity"],
  "instructions": "agents-md",
  "claude_ai_connectors": "off",
  "permission_rules": "local",
  "destructive_tools": {"supabase": ["delete_project"]},
  "language": "en"
}
```

| Key | What the strict value adds |
|---|---|
| `agents` | Leave out `gemini-cli` and a repo's `.gemini/` and `GEMINI.md` count as leftovers. |
| `instructions: "agents-md"` | `AGENTS.md` is canonical; every `CLAUDE.md` must be a symlink to it. |
| `claude_ai_connectors: "off"` | claude.ai connectors must be disabled. |
| `permission_rules: "local"` | All permission rules live in `.claude/settings.local.json`; a committed `.claude/settings.json` is a finding. |
| `destructive_tools` | Extra MCP tools that need a deny rule while their server is enabled. |
| `language` | Report language (default: the language you write in). |

The full layout the linter checks is in [`references/policy.md`](skills/agentic-repo-lint/references/policy.md).

## Privacy

Everything runs locally. The scripts read your repo, your agent config (`~/.claude*`, `~/.codex`, `~/.gemini/config`, `~/.agents`) and your session logs. They write only to `~/.agents/lint-runs/` (run history and accepted findings) and, when you approve a fix, to `~/.agents-backup/`. Secret values are never printed: findings name the key, not its value.

## Using the scripts directly

```bash
python3 skills/agentic-repo-lint/scripts/lint.py --repo . [--json] [--save]
python3 skills/agentic-repo-lint/scripts/usage.py --skills a,b --mcp x,y [--days 90]
python3 -m unittest discover -s skills/agentic-repo-lint/tests
```

Standard library only, no dependencies.

## License

[MIT](LICENSE)
