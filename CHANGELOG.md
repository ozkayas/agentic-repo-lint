# Changelog

Versions follow [semver](https://semver.org):

- **Patch** (`2.0.x`): wording fixes; a run looks at the same things.
- **Minor** (`2.x.0`): new areas or kinds of clutter; a run can raise new findings.
- **Major** (`x.0.0`): a change to how a run works or to what the report contains.

## v2.0.0 (2026-11-26)

The skill is now a procedure; the bundled scripts are gone.

- **The report is the product.** A run changes nothing. Applying findings is a separate step, taken only when you ask for it.
- **No enforced layout.** Findings describe what is there, why it costs something and what you could do. Severity levels, the strict rules and the config file `~/.agents/agentic-repo-lint.json` are removed.
- **No bundled scripts.** `lint.py`, `usage.py` and their tests are removed. The agent learns the OS and the agents in use, reads their current documentation where needed, and writes a throwaway script only if the machine calls for one.
- **Verification by a subagent.** Candidate findings are handed over as bare claims to be refuted before they reach the report.
- **Cleanup only.** Permission-risk checks and secret scanning are removed. A key met while reading is noted in one line, never printed.
- **Skill verdicts no longer block the report.** They are part of it.
- `references/policy.md` is replaced by `references/where-to-look.md`; `references/usage-evidence.md` and `references/applying.md` are new.

**Upgrading from v1:** `~/.agents/agentic-repo-lint.json` and `~/.agents/lint-runs/` are no longer read; you can delete them. Findings you accepted with `--accept` are not carried over.

## v1.0.0 (2026-11-26)

First public release.

- `lint.py`: skills, MCP servers, instruction files, permissions, hooks, memory and stale config for Claude Code (every `~/.claude-*` account and enabled plugins), Codex and Antigravity. Stable finding ids, run history, `--accept` with a reason.
- `usage.py`: sessions per skill and per MCP server from Claude Code and Codex logs; subagent logs count toward their parent session.
- Config file `~/.agents/agentic-repo-lint.json` for the opinionated rules; flexible defaults without it.
- Keys found in the wrong place are reported by name, never printed.
