# Agent setup policy

Purpose: keep a repo fit for agent work by cleaning what agents leave behind over time: extra skills, unused MCP servers and services, keys in the wrong place, broken or stale config. It is a cleanup audit, not a security scanner.

The linter checks the layout below. Rules marked **(config)** are off by default and turned on in `~/.agents/agentic-repo-lint.json`; everything else applies to every user. When a rule here changes, update `scripts/lint.py` (`DEFAULTS`, `DESTRUCTIVE_TOOLS`) and its tests in the same change.

## Config

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

| Key | Default | Strict value and what it adds |
|---|---|---|
| `agents` | all four, `gemini-cli` included | Leave out `gemini-cli` and a repo's `.gemini/` and `GEMINI.md` become leftovers. Leave out `claude` and an `AGENTS.md` without a `CLAUDE.md` beside it is not a finding. |
| `instructions` | `auto`: a `CLAUDE.md` with its own content is a finding only beside an `AGENTS.md` (two files that drift) | `agents-md`: `AGENTS.md` is canonical everywhere, and every `CLAUDE.md` points at the `AGENTS.md` beside it. |
| `claude_ai_connectors` | `any` | `off`: claude.ai connectors must be disabled. |
| `permission_rules` | `any` | `local`: every permission rule lives in the repo root's `.claude/settings.local.json`; a committed `.claude/settings.json` is a finding. |
| `destructive_tools` | Firebase's delete tools | Extra MCP tools, by server, that need a deny rule while the server is enabled. |
| `language` | the user's language | Report language. |

## Agents

- **Who reads what:**
  - Claude Code: `CLAUDE.md`, `.claude/skills`, `~/.claude/skills`.
  - Codex: `AGENTS.md`, `.agents/skills`, `~/.agents/skills`.
  - Antigravity: `AGENTS.md`, `.agents/skills`, `.agents/rules/*.md`, `.agents/workflows`, and globally `~/.gemini/config`.
- **`~/.gemini` is live Antigravity config** (`config/`, `skills/`, `antigravity*`), never a leftover. Only a repo's `.gemini/` can be one, and only when Gemini CLI is not in use.
- **More than one Claude account:** extra config dirs `~/.claude-*` (used through `CLAUDE_CONFIG_DIR`) each have their own `settings.json`, `.claude.json` and `CLAUDE.md`. Their `skills` folder should link to `~/.claude/skills`, and their `CLAUDE.md` should match the main one.

## Instructions

- **One source of truth.** `AGENTS.md` and `CLAUDE.md` are never two copies that drift apart. `CLAUDE.md` points at the `AGENTS.md` beside it: a symlink (`ln -s AGENTS.md CLAUDE.md`) or a file that imports it with an `@AGENTS.md` line, plus Claude-only lines if needed. The import also works where symlinks do not, such as Windows checkouts.
- **Rules every agent needs** belong in `AGENTS.md` (or `CLAUDE.md` when Claude is the only agent). `.agents/rules` reaches Antigravity only, and `@imports` reach Claude only.
- **Pointers** name a skill or a path in the repo, not a path on one machine or account.

## Skills

- **Repo skills** live in `.agents/skills` when more than one agent is in use; `.claude/skills` is then one directory symlink to `../.agents/skills`, never per-skill links. A Claude-only repo may keep real skills in `.claude/skills`.
- **Feature skills:** a skill that serves one feature folder lives in that folder's own `.agents/skills`, with a `.claude/skills` link beside it. Claude and Codex load skills from the start directory up to the repo root. A root Claude session loads a feature's skills only once it reads or edits a file in that folder (Claude Code docs, skills.md).
- **A `SKILL.md` under any other folder name**, such as `backend/skills/`, reaches no agent.
- **Global skills** live in `~/.agents/skills` (the `npx skills` layout; its lock is `~/.agents/.skill-lock.json`), with one symlink per skill in `~/.claude/skills`. `~/.claude/skills` stays a real folder: it can also hold tool-managed skills and Claude Code's own `synced/` and `.trash/`, which must not leak to the other agents.
- **Third-party skill sets** are installed globally, not copied into repos.
- **Irrelevant global skills** are hidden per repo with `skillOverrides: {"<name>": "off"}` in `.claude/settings.local.json`; `"user-invocable-only"` keeps one in the `/` menu but out of the agent's listing.
- **claude.ai account skills** are turned off per repo with `"syncClaudeAiSkills": false` when unused.
- **Stale names:** a `skillOverrides` entry for a skill that no longer exists is removed.

## MCP

- **Unused servers** are removed where no agent uses them; `usage.py --mcp` shows the calls per server. A server configured in several places (`mcp-duplicate`) is cleaned in every place at once.
- **Versions:** `npx` packages may stay unpinned (`@latest`). An SDK-based server starts through its version manager's entry point, never a hardcoded `.../versions/x.y.z` path.
- **Keys** reach MCP servers through env vars where the agent supports it: Claude `${VAR}` expansion, Codex `env_http_headers` or `bearer_token_env_var`. A key written in a repo file is an error; one in a private file under `~` is a warning.
- **Auto-start:** `enableAllProjectMcpServers` is never committed in `.claude/settings.json`; the servers you use are listed in `enabledMcpjsonServers` in the local file. Entries naming a server no longer in `.mcp.json` are removed.
- **Destructive MCP tools** are denied while their server is enabled. A server-level deny (`mcp__<server>` or `mcp__<server>__*`) covers every tool.
- **claude.ai connectors** add tools to every session; turn off the ones you do not use (**config** `claude_ai_connectors: "off"` requires `disableClaudeAiConnectors`). A `true` from any settings file wins over a `false` (Claude Code docs, mcp.md).

## Permissions

- **Keys:** a permission rule, settings `env` block or hook command holding a key is cleaned up; the key moves to `.env` or the shell profile.
- **Broad allows:** a bare interpreter (`Bash(npx:*)`, `Bash(python3:*)`, `Bash(node:*)`) runs any code; allow the specific command instead. `Bash(curl:*)`, proxy unsetting, `NODE_TLS_REJECT_UNAUTHORIZED`, `osascript` and `claude mcp` allows are removed.
- **No blanket modes** in config files: no `defaultMode: "bypassPermissions"`, no Codex `danger-full-access` sandbox or `approval_policy = "never"`. Turn them on per session.
- **Stale rules:** one-off commands, rules for MCP servers that are no longer configured, and rules with dead paths are removed. A removed deny rule comes back with its server.
- **Hooks:** a hook whose script is gone, or that serves a tool no longer used, is removed.
- **Where rules live (config `permission_rules: "local"`):** Claude Code reads the local file from the repo root wherever a session starts, but reads `.claude/settings.json` only from the start directory (Claude Code docs, settings.md). If you start agents inside subfolders, rules in the shared file vanish for them, so all rules, `deny` and `ask` included, go in `.claude/settings.local.json`.

## Global settings

- **`autoMode` lives in user settings only,** so its text applies in every repo. Keep repo names in it current; renames leave stale names behind.
- **The agent cannot edit the `autoMode` block:** the auto-mode classifier blocks it as self-modification, so the user runs the edit.
