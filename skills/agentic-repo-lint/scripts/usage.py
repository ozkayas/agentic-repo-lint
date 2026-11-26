#!/usr/bin/env python3
"""Count how often each skill and each MCP server was used, from Claude Code and Codex session logs.

A use is one session in which the agent invoked the skill or read its SKILL.md, or called a tool of the
MCP server. A subagent's log (`<session>/subagents/agent-*.jsonl`) belongs to its parent session. Claude
logs live in ~/.claude/projects and every ~/.claude-*/projects (one per account); Codex logs in
~/.codex/sessions.

Usage: python3 usage.py [--home ~] [--exclude-session <id>] [--skills a,b,c] [--mcp a,b] [--days N] [--json]

The logs run to many GB, so lines are scanned as bytes and only lines holding a marker are parsed.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import time
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from pathlib import Path

SKILL_MD = re.compile(rb"skills/([A-Za-z0-9_-]+)/SKILL\.md")
SKILL_TOOL = re.compile(rb'"name":\s*"Skill",\s*"input":\s*\{\s*"skill":\s*"(?:[\w-]+:)?([^"]+)"')
SLASH_COMMAND = re.compile(rb"<command-name>/?(?:[\w-]+:)?([A-Za-z0-9_-]+)</command-name>")
READ_TOOL = re.compile(rb'"name":\s*"Read"')
# Only real calls: tool definitions also hold `"name": "mcp__…"`, but never right after a tool_use id.
MCP_CALL = re.compile(rb'"type":\s*"tool_use",\s*"id":\s*"[^"]*",\s*"name":\s*"mcp__([A-Za-z0-9_.-]+?)__')
CODEX_MCP_CALL = re.compile(rb'"type":\s*"McpToolCall",\s*"id":\s*"[^"]*",\s*"server":\s*"([^"]+)"')
TIMESTAMP = re.compile(rb'"timestamp":\s*"(\d{4}-\d\d-\d\d)')
CODEX_CALLS = (b'"function_call"', b'"custom_tool_call"', b"exec_command", b'"local_shell_call"')
LISTINGS = (b"skills_instructions", b"<available_skills>")
# A session that touches this many skills is an audit reading them all, not use.
BULK_SESSION = 15


def scan(path: Path, agent: str) -> tuple[dict[str, str], dict[str, str]]:
    """(skill name -> last date, MCP server -> last date) seen in one log."""
    seen: dict[str, str] = defaultdict(str)
    servers: dict[str, str] = defaultdict(str)
    try:
        _scan(path, agent, seen, servers)
    except OSError:  # a live session log can vanish or rotate between listing and reading
        return {}, {}
    return seen, servers


def _scan(path: Path, agent: str, seen: dict[str, str], servers: dict[str, str]):
    with open(path, "rb") as f:
        for line in f:
            # Cheap substring tests first: almost no line names a skill or calls an MCP tool.
            has_md = b"SKILL.md" in line
            mcp = MCP_CALL if agent == "claude" else CODEX_MCP_CALL
            if (b"mcp__" in line if agent == "claude" else b"McpToolCall" in line):
                calls = set(mcp.findall(line))
                if calls:
                    m = TIMESTAMP.search(line)
                    date = m.group(1).decode() if m else ""
                    for server in calls:
                        key = server.decode(errors="ignore")
                        servers[key] = max(servers[key], date)
            if agent == "claude":
                if not (has_md or b'"Skill"' in line or b"<command-name>" in line):
                    continue
            elif not has_md:
                continue
            # Skill listings injected into every session are not uses.
            if any(k in line for k in LISTINGS):
                continue
            hits: set[bytes] = set()
            if agent == "claude":
                if has_md and READ_TOOL.search(line):
                    hits |= set(SKILL_MD.findall(line))
                hits |= set(SKILL_TOOL.findall(line)) | set(SLASH_COMMAND.findall(line))
            elif any(k in line for k in CODEX_CALLS):
                hits |= set(SKILL_MD.findall(line))
            if not hits:
                continue
            m = TIMESTAMP.search(line)
            date = m.group(1).decode() if m else ""
            for name in hits:
                key = name.decode(errors="ignore")
                seen[key] = max(seen[key], date)


def session_of(path: Path) -> str:
    """The session a log belongs to: a subagent's log (`<session>/subagents/agent-*.jsonl`, or deeper as in
    `<session>/subagents/workflows/<run>/agent-*.jsonl`) counts toward its parent session."""
    parts = path.parts
    if "subagents" in parts[:-1]:
        i = len(parts) - 1 - parts[::-1].index("subagents")
        return parts[i - 1]
    return path.stem


def excluded(stem: str, exclude: set[str]) -> bool:
    # Claude names a log `<session-id>`; Codex names it `rollout-<timestamp>-<session-id>`.
    return any(stem == e or stem.endswith("-" + e) for e in exclude)


def changed_since(path: Path, since: float) -> bool:
    try:
        return path.stat().st_mtime >= since
    except OSError:
        return False


def count(home: Path, exclude: set[str], days: int | None = None) -> dict[str, dict]:
    return collect(home, exclude, days)[0]


def count_mcp(home: Path, exclude: set[str], days: int | None = None) -> dict[str, dict]:
    return collect(home, exclude, days)[1]


def collect(home: Path, exclude: set[str], days: int | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """(skill uses, MCP server uses), each name -> {"claude": sessions, "codex": sessions, "last": date}."""
    since = time.time() - days * 86400 if days else 0
    logs = []
    for config in [home / ".claude"] + sorted(home.glob(".claude-*")):
        if (config / "projects").is_dir():
            logs += [("claude", f) for f in (config / "projects").rglob("*.jsonl")]
    codex = home / ".codex/sessions"
    if codex.is_dir():
        logs += [("codex", f) for f in codex.rglob("*.jsonl")]
    logs = [(agent, f) for agent, f in logs if not excluded(session_of(f), exclude) and changed_since(f, since)]
    paths, agents = [f for _, f in logs], [a for a, _ in logs]
    if len(logs) > 8:  # scanning is CPU-bound; one process per core cuts the wall time several times over
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            found = list(pool.map(scan, paths, agents, chunksize=16))
    else:
        found = list(map(scan, paths, agents))
    # Merge each session's logs (the main one and its subagents'), keeping the latest date per name.
    sessions: dict[tuple[str, str], tuple[dict, dict]] = {}
    for (agent, f), (names, servers) in zip(logs, found):
        merged = sessions.setdefault((agent, session_of(f)), ({}, {}))
        for into, part in zip(merged, (names, servers)):
            for name, date in part.items():
                into[name] = max(into.get(name, ""), date)
    use: dict[str, dict] = defaultdict(lambda: {"claude": 0, "codex": 0, "last": ""})
    mcp: dict[str, dict] = defaultdict(lambda: {"claude": 0, "codex": 0, "last": ""})
    for (agent, _), (names, servers) in sessions.items():
        for table, part in ((use, {} if len(names) > BULK_SESSION else names), (mcp, servers)):
            for name, date in part.items():
                table[name][agent] += 1
                table[name]["last"] = max(table[name]["last"], date)
    return dict(use), dict(mcp)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--exclude-session", action="append", default=[],
                    help="session id to skip, e.g. the current audit session")
    ap.add_argument("--skills", help="comma-separated names to report, including unused ones")
    ap.add_argument("--mcp", help="comma-separated MCP server names to report, including unused ones")
    ap.add_argument("--days", type=int, help="only logs changed in the last N days (default: all logs)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    use, mcp = collect(Path(args.home).expanduser(), set(args.exclude_session), args.days)
    zero = {"claude": 0, "codex": 0, "last": ""}
    if args.skills:
        use = {n: use.get(n, zero) for n in (x.strip() for x in args.skills.split(",")) if n}
    if args.mcp:
        mcp = {n: mcp.get(n, zero) for n in (x.strip() for x in args.mcp.split(",")) if n}
    # A zero inside a window does not mean never used; say which window the counts cover.
    window = f"last {args.days} days" if args.days else "all logs"
    if args.json:
        print(json.dumps({"window": window, "skills": use, "mcp": mcp}, indent=2, sort_keys=True))
        return
    print(f"window: {window}")
    for title, table in (("skills", use), ("mcp servers", mcp)):
        print(f"\n{title}:")
        for name, u in sorted(table.items(), key=lambda kv: kv[1]["last"], reverse=True):
            print(f"{name:45} claude {u['claude']:3}  codex {u['codex']:3}  last {u['last'] or '-'}")


if __name__ == "__main__":
    main()  # the __main__ guard is required: ProcessPoolExecutor re-imports this module on macOS
