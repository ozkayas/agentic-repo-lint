# Where to look

The areas of an agent setup where clutter builds up. Each one says where to look, what clutter looks like there, and why it matters, so the report can give the reason with the finding.

The paths are **starting points** for Claude Code, Codex and Antigravity as they were when this was written. Agents move things between releases: trust what you find on the machine and in the agent's current documentation over this file. For another agent (Cursor, Copilot, Windsurf, Gemini CLI…), find its equivalent of each area in its docs.

Nothing here is a rule the user must follow. Each "why" is the cost of leaving things as they are; whether to pay it is their choice.

## Instruction files

**Look at:** `AGENTS.md`, `CLAUDE.md` and `GEMINI.md` at the repo root and in subfolders; `CLAUDE.local.md`; `.claude/rules/`, `.agents/rules/`; the global ones (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`); files they pull in with `@path` imports.

**Who reads what:** Codex and Antigravity read `AGENTS.md`. Claude Code reads `CLAUDE.md`; recent versions also load `AGENTS.md` as project instructions when the project has no `CLAUDE.md` (a built-in option, `instructionFiles`, can make it load beside `CLAUDE.md` or not at all). Confirm on this machine, from the current docs or from the files the session reports as loaded, before judging the rows below. `@path` imports are followed by Claude only; the other agents see the line as text. `.agents/rules` reaches Antigravity only.

| Clutter | Why it matters |
|---|---|
| **duplicate**: `AGENTS.md` and `CLAUDE.md` side by side, each with its own content | Someone updates one and forgets the other, and agents then work by different rules without anyone noticing. One file can point at the other: a symlink, or a one-line `@AGENTS.md` import (which also works where symlinks do not). |
| **heavy**: a `CLAUDE.md` that points at `AGENTS.md` (symlink or `@AGENTS.md`) while Claude is set to load `AGENTS.md` beside `CLAUDE.md` | The same text loads twice in every session. Report it only after seeing that setting on the machine; by default Claude loads `AGENTS.md` only where there is no `CLAUDE.md`. |
| **misplaced**: an `AGENTS.md` with no `CLAUDE.md` beside it, when the Claude Code here does not load `AGENTS.md`; a rule every agent needs kept in a place only one agent reads | The agent that cannot see it works without it. |
| **dead**: a path, command or `@import` that no longer exists | The agent goes looking for it, or follows an instruction that cannot work. |
| **stale** lines (*sediment*): facts, tools and decisions that are gone | The agent obeys them. |
| **heavy** lines: a *no-op* (what the model does anyway), a *cache* (restating `package.json`, the directory layout, `--help`) | They load every session and say nothing; a cache also goes stale. |
| **duplicate** lines: the same rule in two files, or two lines that contradict | A one-place change becomes a two-place change; with a contradiction the agent picks one. |
| **misplaced** pointers: a path that exists on one machine or under one account only | It is dead for everyone else. |

## Memory

**Look at:** the agent's memory for this repo. For Claude Code, `~/.claude/projects/<repo path with dashes>/memory/`: an index `MEMORY.md` plus one file per memory. Each extra config directory (`CLAUDE_CONFIG_DIR`) has its own `projects/<repo>/memory/`; label findings with that full path. The same file can be hard-linked into several accounts: compare inodes and count it once.

| Clutter | Why it matters |
|---|---|
| **dead**: an index line whose file is gone | The agent is told a memory exists and cannot read it. |
| **misplaced**: a memory file the index does not list | It is never recalled. |
| **heavy**: an index longer than the agent loads | Everything past the cut is invisible; check the current limit in the docs. |
| **stale** or **duplicate**: a memory that repeats or contradicts an instruction file, or names code that is gone | Memories read as true; an old one overrides the newer file. |

## Skills

**Look at:** repo skills (`.agents/skills`, `.claude/skills`, and the same folders inside feature subfolders); global skills (`~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`, Antigravity's `~/.gemini/config/skills`); skills brought by enabled plugins; skills synced from an account. Find every `SKILL.md` in the repo, wherever it sits.

**What it costs:** each skill an agent may invoke by itself has its description loaded into every session. A skill that only the user can invoke, or that is switched off for the repo, costs nothing there. Record that state for each skill.

| Clutter | Why it matters |
|---|---|
| **unused**: no session has invoked it, or none for months | Its description is paid for in every session. |
| **heavy**: plainly unrelated to this repo's stack, yet listed here | Same cost, and it can fire on the wrong task. A per-repo switch (Claude: `skillOverrides`) hides it here without removing it elsewhere. |
| **misplaced**: a `SKILL.md` in a folder no agent loads (`tools/skills/…`) | Nobody will ever run it. |
| **misplaced**: a real folder in one agent's skill directory while several agents are in use | The other agents never see it. |
| **duplicate**: the same skill in the repo and globally | The copies drift, and agents disagree about which one wins. |
| **dead**: a skill that names scripts, inputs or folders that are gone; a broken symlink in a skill folder | It fails halfway through. |
| **stale**: a finished one-off, whose outputs exist and whose inputs are gone | It will never run again. |
| **stale**: a setting that names a skill which no longer exists | Leftover of a removal. |
| A description missing or longer than the agent shows | The agent cannot tell when to use it; check the current limit. |

Before calling a skill removable, check whether it holds the only copy of a script that a README or another script runs.

A plugin is one switch for many skills and often an MCP server too: judge it as a whole as well as skill by skill.

## MCP servers

**Look at:** the repo's `.mcp.json`; Claude's global and per-project servers (`~/.claude.json`); servers from enabled plugins; Codex (`~/.codex/config.toml`, the repo's `.codex/config.toml`); Antigravity (`~/.gemini/config/mcp_config.json`); connectors that come from the user's account rather than a file.

| Clutter | Why it matters |
|---|---|
| **unused**: no session ever called one of its tools | It starts a process in every session, and its tool definitions cost context unless the agent defers them until needed (check the agent's current docs; Claude Code can defer them). |
| **heavy**: a server for another project's stack, enabled globally | Same cost in a repo that can never use it. |
| **duplicate**: one server configured in several files or for several agents | Removing it, or changing its key, has to be repeated in each place. Report all the places as one finding. |
| **stale**: an enable or disable list naming a server that is no longer defined | Leftover of a removal. |
| **dead**: a command that points at a versioned install path (`…/versions/1.2.3/…`) | It breaks at the next update. |

## Settings and permission rules

**Look at:** `.claude/settings.json`, `.claude/settings.local.json`, the user settings, Codex's config. Read them for leftovers, not for risk.

| Clutter | Why it matters |
|---|---|
| **stale**: permission rules for an MCP server that is no longer configured, or for a path that is gone | Dozens of these hide the few rules that still matter. |
| **stale**: long one-off commands saved as rules when the user clicked "always allow" | They match nothing again. Group them as one finding. |
| **dead**: a settings file that does not parse | The agent treats it as empty, silently. |
| **misplaced**: a personal settings file tracked by git | One person's choices are applied to everyone who clones. |
| **stale**: backup copies beside the live file (`settings.json.bak-…`) | Nobody knows which one is current; they can also hold old keys. Compare them with the live file through the value-free filter from the ground rules. |

## Hooks

**Look at:** the `hooks` block of each settings file; `~/.codex/hooks.json`; `~/.gemini/config/hooks.json`.

| Clutter | Why it matters |
|---|---|
| **dead**: the script it runs is gone | It fails on every event. |
| **stale**: it serves a tool that is no longer installed, or nobody recognises it | It runs on every event for nothing. Ask the user about the ones you cannot place. |

## Subagents, commands, plugins

**Look at:** `.claude/agents`, `.claude/commands` and their global counterparts; the enabled plugins.

The same kinds apply: **unused** definitions, **dead** paths inside them, **duplicate**s of a skill that replaced them, plugins enabled and never used.

## What agents left in the repo

**Look at:** the repo root and dot-folders, tracked and untracked.

| Clutter | Why it matters |
|---|---|
| **stale**: plan, scratch, handoff and "issues" folders written by an agent for a finished piece of work, still tracked | An agent that searches the repo reads old decisions as current ones. |
| **stale**: registered worktrees whose folder is gone; empty agent folders | Noise in every listing. |
| **stale**: config folders for an agent that is no longer used here | Ask before calling it a leftover: only the user knows which agents they use. |
| **misplaced**: agent output in the tree that is neither tracked nor ignored (diff dumps, logs, screenshots) | It ends up in a commit by accident. |

## More than one account or config directory

Only when the machine has them (for Claude, extra directories used through `CLAUDE_CONFIG_DIR`).

| Clutter | Why it matters |
|---|---|
| **duplicate**: instructions, skills or settings that differ between the accounts | The agent behaves differently depending on which account started it, and the user has usually forgotten which copy is newer. |
