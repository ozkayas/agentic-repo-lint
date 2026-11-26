"""Fixture tests for usage.py: which log lines count as a skill use, and which sessions are skipped.

Run: python3 -m unittest discover -s ~/.agents/skills/agentic-repo-lint/tests
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import usage  # noqa: E402

CODEX_ID = "00000000-0000-7000-8000-000000000001"


def jsonl(path: Path, *records: dict, age_days: float = 0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))


class Usage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        projects = self.home / ".claude/projects/-repo"
        jsonl(projects / "sess-a.jsonl",
              {"timestamp": "2026-11-01T10:00:00Z", "message": {"content": [
                  {"type": "tool_use", "name": "Skill", "input": {"skill": "plugin:tdd"}}]}},
              {"timestamp": "2026-11-02T10:00:00Z", "message": {"content": [
                  {"type": "tool_use", "name": "Read", "input": {"file_path": "/h/.agents/skills/grilling/SKILL.md"}}]}},
              {"timestamp": "2026-11-03T10:00:00Z", "message": {"content": "<command-name>/commit-message</command-name>"}},
              {"timestamp": "2026-11-04T10:00:00Z", "message": {"content": "<available_skills> skills/unused/SKILL.md"}})
        jsonl(projects / "sess-current.jsonl",
              {"timestamp": "2026-11-05T10:00:00Z", "message": {"content": [
                  {"type": "tool_use", "name": "Skill", "input": {"skill": "tdd"}}]}})
        jsonl(self.home / ".claude-acct2/projects/-repo/sess-old.jsonl",
              {"timestamp": "2025-01-01T10:00:00Z", "message": {"content": [
                  {"type": "tool_use", "name": "Skill", "input": {"skill": "ancient"}}]}}, age_days=400)
        jsonl(self.home / f".codex/sessions/2026/11/01/rollout-2026-11-01T10-00-00-{CODEX_ID}.jsonl",
              {"timestamp": "2026-11-01T10:00:00Z", "type": "function_call", "name": "exec_command",
               "arguments": "{\"cmd\": \"cat ~/.agents/skills/tdd/SKILL.md\"}"})

        # Subagents of sess-a: their logs live under sess-a/subagents/ and belong to that session.
        for n in ("agent-1", "agent-2"):
            jsonl(projects / "sess-a/subagents" / f"{n}.jsonl",
                  {"timestamp": "2026-11-06T10:00:00Z", "message": {"content": [
                      {"type": "tool_use", "name": "Skill", "input": {"skill": "tdd"}},
                      {"type": "tool_use", "id": "toolu_1", "name": "mcp__context7__query-docs", "input": {}}]}})
        jsonl(projects / "sess-a/subagents/workflows/wf_1/agent-3.jsonl",
              {"timestamp": "2026-11-06T10:00:00Z", "message": {"content": [
                  {"type": "tool_use", "name": "Skill", "input": {"skill": "tdd"}}]}})
        jsonl(projects / "sess-mcp.jsonl",
              {"timestamp": "2026-11-07T10:00:00Z", "tools": [{"name": "mcp__firebase__deploy"}]},  # a definition, not a call
              {"timestamp": "2026-11-07T10:00:00Z", "message": {"content": [
                  {"type": "tool_use", "id": "toolu_2", "name": "mcp__dart-tools__analyze", "input": {}}]}})
        jsonl(self.home / f".codex/sessions/2026/11/02/rollout-2026-11-02T10-00-00-{CODEX_ID[:-1]}2.jsonl",
              {"timestamp": "2026-11-02T10:00:00Z", "item": {"type": "McpToolCall", "id": "call_1",
                                                           "server": "context7", "tool": "resolve"}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_subagent_logs_count_toward_their_parent_session(self):
        use = usage.count(self.home, set())
        self.assertEqual(use["tdd"]["claude"], 2)  # sess-a (with its two subagents) and sess-current
        self.assertEqual(usage.count(self.home, {"sess-a"})["tdd"]["claude"], 1)

    def test_mcp_calls_are_counted_per_server(self):
        mcp = usage.count_mcp(self.home, set())
        self.assertEqual(mcp["context7"], {"claude": 1, "codex": 1, "last": "2026-11-06"})
        self.assertEqual(mcp["dart-tools"]["claude"], 1)
        self.assertNotIn("firebase", mcp)

    def test_counts_each_kind_of_use(self):
        use = usage.count(self.home, set())
        self.assertEqual(use["tdd"], {"claude": 2, "codex": 1, "last": "2026-11-06"})
        self.assertEqual(use["grilling"]["claude"], 1)
        self.assertEqual(use["commit-message"]["claude"], 1)
        self.assertEqual(use["ancient"]["claude"], 1)
        self.assertNotIn("unused", use)

    def test_vanished_log_is_skipped(self):
        os.symlink(self.home / "nowhere.jsonl", self.home / ".claude/projects/-repo/vanished.jsonl")
        self.assertEqual(usage.count(self.home, set())["tdd"]["claude"], 2)
        self.assertEqual(usage.scan(self.home / "missing.jsonl", "claude"), ({}, {}))

    def test_exclude_claude_session(self):
        self.assertEqual(usage.count(self.home, {"sess-current"})["tdd"]["claude"], 1)

    def test_exclude_codex_session_by_id(self):
        self.assertEqual(usage.count(self.home, {CODEX_ID})["tdd"]["codex"], 0)

    def test_days_window(self):
        use = usage.count(self.home, set(), days=90)
        self.assertNotIn("ancient", use)
        self.assertEqual(use["tdd"]["claude"], 2)

    def test_window_is_printed(self):
        for argv, window in ((["--days", "90"], "window: last 90 days"), ([], "window: all logs")):
            out = io.StringIO()
            sys.argv = ["usage.py", "--home", str(self.home), "--skills", "tdd,never-used", *argv]
            with redirect_stdout(out):
                usage.main()
            self.assertIn(window, out.getvalue())
            self.assertIn("never-used", out.getvalue())
            self.assertIn("context7", out.getvalue())  # MCP servers are reported too


if __name__ == "__main__":
    unittest.main()
