---
name: agentic-repo-lint
description: "Audit what coding agents see in a repo and in the global agent config (skills, MCP servers, instruction files, permissions, memory), then report numbered findings (A-1, A-2…) and fix only what the user approves. Use it to clean up what agents leave behind: unused skills and MCP servers, keys in the wrong place, stale or broken config."
disable-model-invocation: true
---

# Agentic repo lint

Periodic cleanup audit of the agent setup around one repo: whatever reaches Claude Code, Codex and Antigravity there. Agents that run for months leave clutter behind; this finds it. It is not a security scanner.

The target layout is [references/policy.md](references/policy.md). Read it before step 2; every finding is a gap between the repo and that policy, or a line that wastes context.

- **Config:** `~/.agents/agentic-repo-lint.json` (optional) turns the opinionated rules on; without it the defaults in policy.md apply. `inventory.config` shows what is in effect.
- **Paths:** `${CLAUDE_SKILL_DIR}` is this skill's folder (the one holding this SKILL.md). Agents that do not fill it in use that folder's path instead.
- **Language:** write the report and talk to the user in their language, or in the config's `language` when it is set.

## How to work

This skill is the procedure; the two scripts are instruments. Pick an instrument for the question in front of you, read what it returns as **leads**, and settle every lead against the **intent** of its rule in policy.md.

- `lint.py` answers "where does this setup drift from the layout?" Open the file behind each lead and decide: a real finding, explained away, or a lint bug to note in the report.
- `usage.py` answers "is this skill or MCP server still used?" A count is evidence to weigh beside git history and the repo's stack; the verdict is yours.
- **Intent outranks mechanism.** A `CLAUDE.md` that imports `@AGENTS.md` keeps one source of truth as surely as a symlink does. On Windows, or with an agent the scripts do not know, judge by what the rule protects. Fix commands are written for a POSIX shell; translate them to the user's shell.
- **The scripts see Claude Code, Codex and Antigravity only.** When the repo also serves another agent (`.cursor/`, `.github/copilot-instructions.md`, `.windsurf/`…), look at its config yourself and say in the report header that the script did not cover it.
- What no script can see is yours to judge: whether a skill is still needed, whether an instruction line is stale, whether a server belongs to this repo. Steps 2 and 3 are that work.

Scope: the git root of the current directory, plus the global config it inherits (`~/.claude`, `~/.claude-*`, `~/.agents`, `~/.claude.json`, `~/.codex`, `~/.gemini/config`). A path given with the request replaces the current directory.

**Never echo a secret.** Settings, `.claude.json`, MCP `env` blocks and hook commands can hold keys and tokens. When you read or back up these files (steps 3 and 5), quote no secret value, or any part of one, in the report, a command or a commit; write `<redacted>` in its place. Validate JSON silently (`jq empty <file>`), never by printing the file. When a key turns up in a place it should not be, say once in the report that it may need rotating if that file ever left the machine.

## 1. Mechanical check

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/lint.py" --repo <repo> --json --save
```

- The script reads the repo and the config only. It writes in `~/.agents/lint-runs/<repo>-<hash>/` and nowhere else: `--save` stores the run there and `--accept` records accepted findings there. On the first such write it moves the runs of an older `lint-runs/<repo>/` folder into it. Every run is compared with the last saved one.
- Each finding has an `id` (short and stable, used by `--accept`), a `code` (rule), `severity` (`error`, `warn`, `info`), `where`, `message`, `fix` and `status` (`new` or `persisting`). `resolved` lists findings of the previous run that are gone.
- `accepted` lists findings the user chose to keep, each with its `reason`; they stay out of `findings` and the counts.
- `inventory` holds what the agents see:
  - every skill Claude can load, with its description size and `state`: `listed`, `name-only`, `off` or `user-only` (only `listed` and `name-only` count toward the token estimate); plugin skills appear as `<plugin>:<skill>`;
  - the MCP servers of Claude (every account and enabled plugins), Codex and Antigravity, each with `agent`, `source` and `enabled`;
  - the claude.ai connector and skill-sync switches, and the size of the always-loaded instruction files (rules without `paths:` and `CLAUDE.local.md` included);
  - `hooks` (commands, redacted), `claude_rules`, `agents`, `commands`, `plugins`, `imports` (`@path` in instruction files), and `codex` (config files, the repo's trust level).
- Codex's `config.toml` needs Python 3.11+ (`tomllib`); an older `python3` reports `codex-config` and skips it.
- If `lint.py` crashes, show the user the last lines of the traceback and do steps 2-4 by hand from policy.md; propose the `lint.py` fix in the report.
- After any change to `scripts/lint.py`, run its tests first: `python3 -m unittest discover -s "${CLAUDE_SKILL_DIR}/tests"`.

## 2. Skill cleanup

Every skill an agent can load here gets a verdict: **keep**, **fix**, **move** or **delete**. The scope covers:

- the root `.agents/skills`;
- every feature's own `.agents/skills`;
- folders that `skill-misplaced` reports;
- the global skills in `inventory.skills`.

1. **Usage.** Start it in the background (it scans every session log, often many GB) and do step 3 meanwhile; wait for its counts before judging any skill:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/usage.py" --exclude-session "${CLAUDE_SESSION_ID}" --skills a,b,c --mcp x,y`
   - `--skills` and `--mcp` take comma-separated names (the skills in scope, the servers in `inventory.mcp_servers`). Outside Claude Code, drop `--exclude-session` when you do not know your session id.
   - It counts the sessions in which Claude or Codex used each skill or called each MCP server's tools; a subagent's log counts toward its parent session. Its first line names the window: all logs, or the last N days with `--days N`. A zero inside a window is not "never used".
   - Pair each skill count with `git log --follow -- <skill>/SKILL.md`. A creator skill can be maintained in git while no agent session reads it, so a low count alone proves nothing.
2. **Judge each skill.** For a global skill, start from its description, its usage count and its relevance to the repo (item 5). Read the whole SKILL.md only for repo skills and for global skills that may need **fix**, **move** or **delete**; a plainly relevant, used global skill is a **keep** without reading its body. For each skill you read, judge:
   - **Works:** the inputs, scripts and folders it names exist. `skill-dead-ref` is the first pass; paths written in prose need your own check.
   - **Still needed:** a skill whose outputs exist and whose sources are gone is a finished one-off.
   - **Quality:**
     - no-ops ("you are an expert…");
     - schemas copied into the skill that have drifted from the real data;
     - steps that contradict each other.
   - **Placement:**
     - A skill that serves one feature lives in that feature's `.agents/skills`.
     - Repo-wide audits live at the root.
     - Third-party skill sets live globally only.
3. **Before a delete:** check whether the skill holds the only copy of a script that a README, an AGENTS.md or another script runs. If it does, move that script to the feature's `scripts/` and repoint the docs, so the delete removes the skill and nothing else.
4. **Present one skill at a time.** A group is fine when its members share a verdict and a reason. Give:
   - what it does;
   - its last use;
   - what works and what is broken;
   - your verdict and why.

   Then wait for the answer before presenting the next skill. Record the verdict; delete or move nothing yet.
5. **Global skills:**
   - **Relevance:** learn the repo's stack from its manifests (`pubspec.yaml`, `package.json`, `pyproject.toml`…) and its top folders. A global skill whose description has nothing to do with that stack is a candidate. Ask instead of guessing when a skill could plausibly be used here.
   - **Removal changes every repo.** Offer `skillOverrides` `"off"` for this repo as the alternative.

Done when every skill in scope has a verdict the user accepted. The verdicts are applied in step 5, with the other approved fixes.

## 3. Judgement review

The script cannot judge relevance or wording. Do these yourself, reading the files:

1. **Instruction files.**
   - Read: the repo's root `AGENTS.md` and every nested one, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, and the project's `MEMORY.md` with each memory file.
   - Flag a line when it is:
     - a *no-op*: something the model does by default;
     - a *cache*: it restates `pubspec.yaml`, `package.json` or the directory layout;
     - *sediment*: stale facts, or tools and paths that are gone;
     - *duplication*: the same rule in two files;
     - a *contradiction*: it conflicts with another file, or with a memory.
   - Pointers bound to one machine or account (for example `~/.claude-work/...`) are findings too.
2. **MCP relevance.** An enabled server in `inventory.mcp_servers` that the repo never needs, or that `usage.py --mcp` shows unused, is a finding, for every agent. `mcp-duplicate` shows every place to clean in one go.
3. **Hooks.** Look over `inventory.hooks` for leftovers: a hook for a tool that is gone, or one nobody recognises. Ask the user about those; this is cleanup, not a security audit.
4. **Script findings.** Confirm each one before reporting it; `info` findings may be dropped when they carry nothing actionable. A false positive is a lint bug: note it in the report and propose the `lint.py` fix.

Done when every instruction file, MCP server and hook has been looked at, and each script finding is confirmed or explained away.

## 4. Report

Skill verdicts were settled one by one in step 2. List them once under their own heading (skill, verdict, action), so step 5 applies them with the rest; the numbered findings cover everything else.

1. **Header, one line:** repo, remote, skills listed to Claude with their estimated tokens, MCP servers, and whether connectors and skill sync are on or off.
2. **Findings:** numbered `A-1`, `A-2`…, errors first.
   - Each one gives where, what, why it matters, and the proposed fix.
   - Mark each 🆕 (new) or ↺ (persisting). List `resolved` findings at the end as ✅.
3. **Grouping:** findings with one shared fix (for example ten stale permission rules) become one item.
4. **Ending:** close by asking which items to apply, for example "all", "A-1, A-3" or "all but A-2".

## 5. Fixes, after approval only

- Apply exactly the approved items, including the skill verdicts from step 2.
- **Kept on purpose:** when the user decides to keep a finding as it is, record it by the `id` the script prints, so later runs stop raising it: `python3 "${CLAUDE_SKILL_DIR}/scripts/lint.py" --repo <repo> --accept <id> --reason '<the user's reason>'`. One `--accept` takes one finding. For a group, run it once per id; `--accept-prefix '<code>|<where>'` takes every match and prints them, so use it only when the user approved the whole group.
- **Skill deletes and moves:**
  - Before deleting a skill folder that git does not track, copy it to `~/.agents-backup/skills/<stamp>/`.
  - A skill listed in `~/.agents/.skill-lock.json` was installed by the skills CLI: remove it with `npx skills remove -g <name>`, never by hand.
  - Otherwise delete the folder and its link in `~/.claude/skills` (and in each `~/.claude-*/skills` that is a real folder).
  - Afterwards clear the references to it from READMEs, AGENTS.md and `.codex/rules`.
- **Before overwriting a file,** look at its current content.
- **Before rewriting a settings file,** back it up to `~/.agents-backup/settings/<stamp>/` (`mkdir -p` it, then `chmod 700 ~/.agents-backup`; not `/tmp`, settings can hold secrets). Keep JSON valid: check it with `jq empty <file>`, or `python3 -m json.tool <file> >/dev/null` when `jq` is missing; never print the file.
- **Protected paths:** the sandbox blocks writes to `.claude/settings*.json`, `.claude/skills`, `~/.claude/skills` and `~/.claude/settings.json`. Retry those writes outside the sandbox, which goes through a permission prompt.
- **`autoMode`:** the classifier refuses edits to the `autoMode` block. Back the file up first (see the settings bullet above), then give the user one exact line to run, built on this template (BSD `sed -i` differs from GNU, and regex edits break JSON). Set the path to the account the finding names (`~/.claude/settings.json`, `~/.claude-<name>/settings.json`) and replace `EDIT` with the change, e.g. `d["autoMode"]["environment"].remove("…")`:

  ```
  ! python3 -c 'import json,os,tempfile; p=os.path.expanduser("~/.claude/settings.json"); d=json.load(open(p)); EDIT; f=tempfile.NamedTemporaryFile("w",dir=os.path.dirname(p),delete=False); json.dump(d,f,indent=2,ensure_ascii=False); f.write("\n"); f.close(); os.replace(f.name,p)'
  ```
- **Repo changes:** stage them and offer a commit holding only these files. Any unrelated work in the tree stays out of it.
- **Re-run step 1** with `--save`, so the next audit starts from the fixed state. Report what changed.
