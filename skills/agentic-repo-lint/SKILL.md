---
name: agentic-repo-lint
description: "Cleanup audit of what coding agents load in a repo (skills, MCP servers, instruction files, settings, hooks, memory). Produces a report of clutter that built up over time: unused, misplaced, stale or duplicated items, each with the reason it matters."
disable-model-invocation: true
---

# Agentic repo lint

A cleanup audit for a repo that coding agents have worked in for a while. The product is a **report** of **clutter**: things that reach an agent in this repo and no longer earn their place. The user reads the report and decides; changing anything is a separate, optional step.

**Clutter** comes in six kinds. Name the kind in every finding:

- **unused**: nothing has called it for a long time;
- **misplaced**: it sits where the agent that needs it cannot see it, or where an agent that does not need it pays for it;
- **stale**: it describes tools, paths or decisions that are gone;
- **duplicate**: one meaning kept in two places, which drift apart;
- **dead**: a pointer, link or hook whose target does not exist;
- **heavy**: it loads into every session and costs more context than its use justifies.

## Ground rules

- **Observe, explain, leave the choice.** A finding says what is there, why it costs something, and what the user could do. Layout preferences (one instruction file or two, where skills live) are the user's; report the cost of the current choice, never a violation.
- **Scope is what reaches an agent started in this repo:** the repo's own files plus the global config it inherits. Mark each finding **this repo** or **every repo**, because a global change is felt everywhere.
- **Read-only.** The audit changes no file. Scratch scripts and notes go in a temp directory.
- **Cleanup, not security.** Leave permission risk and secret hunting to other tools.
- **Read config without its values.** Settings files, MCP configs, `.claude.json`, hook definitions and their backups can hold keys, and not only under names like `token`: a key can sit inside a command argument (`--header "X-Api-Key: …"`, `--api-key=…`) or a URL. Read these files in two passes. First print only the shape: key paths with each value's type and length (a short script that walks the JSON or TOML). Then print the single values you need to judge an item, and only booleans, numbers, a bare command name or a file path. For `args`, `url`, `env` and `headers`, print an element only after masking whatever follows a `:` or `=` in it. The same holds for session logs: print the names, dates and counts you matched, never the surrounding line. When a key turns up, write one line naming the file and the key's name, with `<redacted>` for the value. If a value reaches your output anyway, say so in the report. Give the verifying subagent the same rule.
- **Language:** write the report in the language the user writes in.

## 1. Orient

Learn the ground before judging anything on it.

- The OS and shell, and which agents are in use here: look for their config in the repo and in the home directory.
- For each agent in use, where it loads instruction files, skills, MCP servers, settings, hooks and memory **on this machine**, and where it keeps session logs. [references/where-to-look.md](references/where-to-look.md) gives starting points for Claude Code, Codex and Antigravity. These paths and rules change between releases: when the reference and the machine disagree, or the agent is one the reference does not cover, read that agent's current documentation before going on.
- The repo's stack, from its manifests and top-level folders, so relevance can be judged later.

Done when you can say, for each agent in use, where each of those six things lives here, and which agents or areas you could not cover.

## 2. Inventory

List everything that reaches an agent in this repo, area by area, following [references/where-to-look.md](references/where-to-look.md). For each item record where it lives, which agents see it, and what it costs when it is always loaded (characters ÷ 4 approximates tokens).

How you collect it is your call. A handful of files you read directly. Dozens of skills or several config layers are worth a throwaway script, written for this machine and kept in the temp directory.

Done when every area in the reference has its items listed, or "none".

## 3. Usage evidence

For every skill and MCP server in the inventory, find out how many sessions used it, when it was last used, and whether it was ever used in this repo. [references/usage-evidence.md](references/usage-evidence.md) says what counts as a use and which log lines only look like one. Pair each count with `git log` for the item's files: something maintained yesterday is not abandoned.

Done when each skill and server has a count and a last-used date, or an explicit "no evidence" with the reason (for example, an agent whose logs you cannot read).

## 4. Judge

Go through the inventory and ask of each item which kind of clutter it might be. The reference lists what each kind looks like per area and why it matters. Open the file behind every **candidate** before keeping it; a count or a filename is a lead, not a conclusion.

For instruction files and memory, read every line: clutter there is sentence-sized.

Done when every inventory item is either a candidate with its evidence written down or cleared.

## 5. Verify

A candidate becomes a **finding** only after a second look by someone who did not form it.

Hand the candidates to a subagent as bare claims with their locations ("`X` at `path` is never used"; "`A.md` line 12 names `scripts/y.sh`, which does not exist"), without your reasoning. Ask it to try to refute each one by reading the files and logs itself, and to answer **confirmed**, **refuted** or **cannot tell**, with what it saw. Give it the ground rules above; it is read-only too.

Drop what it refutes. Keep "cannot tell" items in the report marked **unverified**. If you cannot start a subagent, re-check each candidate against its file yourself and say in the report that verification was not independent.

Done when every candidate has a verdict.

## 6. Report

1. **Header, one line:** repo, OS, agents covered, and the load an agent carries here: skills listed with their estimated tokens, MCP servers, size of always-loaded instructions.
2. **Findings**, numbered `A-1`, `A-2`…, ordered by what they cost the agent, heaviest first. One finding per root cause: the same server configured in four places is one finding. Each gives:
   - **what and where**, with the kind of clutter;
   - **evidence**: the count, date or line that shows it;
   - **why it matters**;
   - **an option**: what the user could do, and a second one where a real alternative exists (hide a global skill in this repo instead of removing it everywhere);
   - **reach**: this repo or every repo.
3. **Clean:** one line naming the areas you looked at and found nothing in.
4. **Not covered:** agents, logs or files you could not read, and anything left unverified.

Close by saying that nothing was changed. If the user then asks you to apply some of it, follow [references/applying.md](references/applying.md).
