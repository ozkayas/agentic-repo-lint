# Changelog

Versions follow [semver](https://semver.org):

- **Patch** (`1.0.x`): bug fixes; the findings you already have stay the same.
- **Minor** (`1.x.0`): new checks or config options; a run can raise new findings.
- **Major** (`x.0.0`): a change that breaks your config or the findings you accepted (`accepted.json`), such as a renamed finding code or a new id format.

## v1.0.0 (2026-11-26)

First public release.

- `lint.py`: skills, MCP servers, instruction files, permissions, hooks, memory and stale config for Claude Code (every `~/.claude-*` account and enabled plugins), Codex and Antigravity. Stable finding ids, run history, `--accept` with a reason.
- `usage.py`: sessions per skill and per MCP server from Claude Code and Codex logs; subagent logs count toward their parent session.
- Config file `~/.agents/agentic-repo-lint.json` for the opinionated rules; flexible defaults without it.
- Keys found in the wrong place are reported by name, never printed.
