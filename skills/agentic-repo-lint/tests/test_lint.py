"""Fixture tests for lint.py: a seeded repo must trip every rule, a clean one none.

Run: python3 -m unittest discover -s ~/.agents/skills/agentic-repo-lint/tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lint  # noqa: E402

PUBLIC_DEFAULTS = dict(lint.DEFAULTS)
# Most tests run the strict profile, the one a user sets in ~/.agents/agentic-repo-lint.json to enforce
# the full layout; the Defaults class checks what a stranger gets with no config file.
STRICT = {"agents": ["claude", "codex", "antigravity"], "instructions": "agents-md",
          "claude_ai_connectors": "off", "permission_rules": "local"}
lint.DEFAULTS = {**PUBLIC_DEFAULTS, **STRICT}


def write(path: Path, text: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def skill(root: Path, name: str, description: str = "Does a thing.", body: str = ""):
    write(root / name / "SKILL.md", f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n")


def git_repo(path: Path, remote: str = "https://github.com/acme/app.git"):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)


class SeededRepo(unittest.TestCase):
    """Every problem the linter knows, planted once."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        home, repo = root / "home", root / "repo"
        git_repo(repo)

        write(repo / "CLAUDE.md", "real file")                       # link-claude-md (error)
        write(repo / "pkg/AGENTS.md", "see `docs/gone.md`")           # link-claude-md (warn) + doc-refs
        write(repo / "pkg/GEMINI.md")                                 # gemini-cli
        write(repo / ".gemini/settings.json", "{}")                   # gemini-cli
        skill(repo / ".agents/skills", "local-only")
        skill(repo / ".agents/skills", "shared", body="see [ref](references/missing.md)")
        skill(repo / "feat/backend/skills", "old-creator")            # skill-misplaced
        skill(repo / "feat2/.agents/skills", "feature-tool",          # link-repo-skills (nested, no link)
              body="Run:\n```bash\npython3 scripts/gone.py --in data/seed/x.json --out out/new.json\n```\n"
                   "Reads `data/seed/x.json`.")                       # skill-dead-ref
        write(repo / "feat2/out/.keep")
        write(repo / ".claude/settings.json", json.dumps({"permissions": {"deny": ["mcp__firebase__x"]}}))  # perm-scope
        write(repo / ".agents/rules/style.md")                        # antigravity-only
        (repo / ".claude/skills").mkdir(parents=True)                 # link-repo-skills (per-skill dir)
        write(repo / ".mcp.json", json.dumps({"mcpServers": {
            "firebase": {"command": "npx", "args": ["-y", "firebase-tools@latest", "mcp"]},
            "dart": {"command": "dart", "args": ["--sdk", "/x/sdk/versions/3.1.0"]},
        }}))
        write(repo / ".claude/settings.local.json", json.dumps({
            "enabledMcpjsonServers": ["firebase", "dart"],
            "permissions": {"allow": [
                "Bash(curl:*)",
                "Bash(unset HTTPS_PROXY)",
                'Bash(curl "https://x.test/v1?key=AIzaSyB-0123456789abcdefghijklmnopqrstu")',
                "mcp__ios-simulator__screenshot",
                "Bash(cd /Users/nobody/old-path && make)",
            ]},
        }))
        subprocess.run(["git", "-C", str(repo), "add", "-f", ".claude/settings.local.json"], check=True)  # hygiene

        skill(home / ".agents/skills", "shared", body="global copy")  # skill-duplicate (differs)
        skill(home / ".agents/skills", "unlinked")                    # link-global-skills (missing)
        skill(home / ".agents/skills", "Mismatch")                    # skill-frontmatter (name)
        write(home / ".agents/skills/Mismatch/SKILL.md", "---\nname: mismatch\ndescription: x\n---\n")
        skill(home / ".agents/skills", "long", description="x" * 1600)
        (home / ".claude/skills").mkdir(parents=True)
        os.symlink("../../.agents/skills/gone", home / ".claude/skills/gone")  # broken link
        for name in ("shared", "Mismatch", "long"):
            os.symlink(f"../../.agents/skills/{name}", home / ".claude/skills" / name)
        write(home / ".claude/settings.json", json.dumps({
            "disableClaudeAiConnectors": True,
            "autoMode": {"environment": ["Trusted repo: acme/old_name"]},  # global-leak
        }))
        write(home / ".claude-acct2/settings.json", "{}")             # config-drift
        mem = home / ".claude/projects" / lint.re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve())) / "memory"
        write(mem / "MEMORY.md", "- [Gone](gone.md) — x\n")           # memory (missing file)
        write(mem / "orphan.md", "x")                                 # memory (not indexed)

        cls.result = lint.Linter(repo.resolve(), home.resolve()).run()
        cls.codes = {f["code"] for f in cls.result["findings"]}
        cls.messages = "\n".join(f"{f['code']} {f['where']} {f['message']}" for f in cls.result["findings"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_rule_fires(self):
        expected = {
            "link-claude-md", "link-repo-skills", "link-global-skills", "config-drift", "skill-duplicate",
            "skill-frontmatter", "mcp-unpinned", "mcp-destructive", "perm-secret", "perm-risky", "perm-stale",
            "global-leak", "doc-refs", "memory", "gemini-cli", "antigravity-only", "hygiene",
            "skill-misplaced", "skill-dead-ref", "perm-scope",
        }
        self.assertEqual(expected - self.codes, set(), self.messages)

    def test_secret_is_masked(self):
        secret = "AIzaSyB-0123456789abcdefghijklmnopqrstu"
        self.assertNotIn(secret, json.dumps(self.result))

    def test_details(self):
        for needle in (
            "link-repo-skills .claude/skills per-skill links",
            "missing for Claude: local-only, shared",
            "broken link to ../../.agents/skills/gone",
            "~/.claude/skills/unlinked",
            "hardcoded SDK version path",
            "destructive tools without a deny rule",
            "MCP server `ios-simulator` is not configured",
            "path `/Users/nobody/old-path` no longer exists",
            "names repo `acme/old_name`",
            "refers to `docs/gone.md`",
            "links to missing file `references/missing.md`",
            "name `mismatch` differs from folder `Mismatch`",
            "Claude truncates the listing",
            "personal settings file is tracked by git",
            "connectors are off in ~/.claude but on for this account",
            "skill-misplaced feat/backend/skills 1 skill(s) no agent loads: old-creator",
            "link-repo-skills feat2/.claude/skills feat2/.claude/skills is missing",
            "names 2 path(s) that do not exist: `data/seed/x.json`, `scripts/gone.py`",
            "agents started in feat2, pkg miss them",
        ):
            self.assertIn(needle, self.messages)

    def test_unpinned_npx_is_not_a_finding(self):
        self.assertNotIn("firebase-tools@latest", self.messages)

    def test_future_outputs_are_not_dead_refs(self):
        self.assertNotIn("out/new.json", self.messages)

    def test_accepted_findings_leave_the_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            accepted_file = Path(tmp) / "accepted.json"
            result = json.loads(json.dumps(self.result))
            hits = lint.accept(result, accepted_file, "mcp-unpinned|.mcp.json:dart", "kept on purpose", prefix=True)
            self.assertEqual(len(hits), 1)
            result = lint.apply_accepted(result, accepted_file)
            self.assertEqual([f["reason"] for f in result["accepted"]], ["kept on purpose"])
            self.assertFalse(any(f["fingerprint"] in hits for f in result["findings"]))

    def test_ids_are_unique_and_stable(self):
        ids = [f["id"] for f in self.result["findings"]]
        self.assertTrue(all(lint.re.fullmatch(r"[0-9a-f]{8}", i) for i in ids), ids)
        self.assertEqual(len(ids), len(set(ids)), self.messages)
        fps = [f["fingerprint"] for f in self.result["findings"]]
        self.assertEqual(len(fps), len(set(fps)), self.messages)

    def test_accept_by_id_takes_exactly_one(self):
        stale = [f for f in self.result["findings"] if f["code"] == "perm-stale"]
        self.assertGreaterEqual(len(stale), 2)
        with tempfile.TemporaryDirectory() as tmp:
            accepted_file = Path(tmp) / "accepted.json"
            result = json.loads(json.dumps(self.result))
            self.assertEqual(lint.accept(result, accepted_file, "perm-stale|", "x"), [])  # a prefix needs prefix=True
            self.assertEqual(lint.accept(result, accepted_file, stale[0]["id"], "x"), [stale[0]["fingerprint"]])
            result = lint.apply_accepted(result, accepted_file)
            self.assertEqual(len(result["accepted"]), 1)

    def test_accepted_entries_from_message_keys_still_match(self):
        f = next(f for f in self.result["findings"] if f["code"] == "mcp-destructive")
        with tempfile.TemporaryDirectory() as tmp:
            accepted_file = Path(tmp) / "accepted.json"
            write(accepted_file, json.dumps({f"{f['code']}|{f['where']}|{f['message']}": "old entry"}))
            result = lint.apply_accepted(json.loads(json.dumps(self.result)), accepted_file)
            self.assertEqual([a["code"] for a in result["accepted"]], ["mcp-destructive"])


class Secrets(unittest.TestCase):
    """No secret, or any part of one, may reach the printed or saved output."""

    SECRETS = {
        "google": "AIzaSyB-0123456789abcdefghijklmnopqrstu",
        "github": "ghp_" + "a1B2c3D4e5" * 4,
        "bearer": "tok3n.Value_0123456789abcdef",
        "header": "hdrValue0123456789abcdef",
        "basic": "dXNlcjpwYXNzd29yZDEyMzQ1Ng==",
        "urlpass": "hunter2secretpw",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "pem": "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSj",
        "letters": "AbCdEfGhIjKlMnOpQrStUvWxYz",
        "quoted": "supersecretvalue12345",
        "upass": "s3cretPassWord",
        "flag": "flagTokenValue99",
        "aws": "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
    }

    def assert_clean(self, text: str):
        for name, secret in self.SECRETS.items():
            self.assertNotIn(secret, text, name)
            self.assertNotIn(secret[:8], text, f"{name}: prefix leaked")

    def test_redact_every_match(self):
        s = self.SECRETS
        rule = f'Bash(curl "https://x.test/?key={s["google"]}" -H "token={s["github"]}")'
        text, kinds = lint.redact(rule)
        self.assert_clean(text)
        self.assertGreaterEqual(len(kinds), 2)

    def test_redact_header_and_url_forms(self):
        s = self.SECRETS
        for rule in (
            f'Bash(curl -H "Authorization: Bearer {s["bearer"]}" https://api.test)',
            f'Bash(curl -H "x-api-key: {s["header"]}" https://api.test)',
            f'Bash(curl -H "Authorization: Basic {s["basic"]}" https://api.test)',
            f'Bash(git clone https://me:{s["urlpass"]}@git.test/r.git)',
            f'Bash(curl -H "Cookie: session={s["jwt"]}")',
            "Bash(echo -----BEGIN OPENSSH PRIVATE KEY----- > k)",
            f'Bash(printf "-----BEGIN PRIVATE KEY-----\\n{s["pem"]}\\n-----END PRIVATE KEY-----")',
            f'Bash(printf "-----BEGIN RSA PRIVATE KEY-----\\n{s["pem"]}")',
            f'Bash(curl -H "Authorization: Bearer {s["letters"]}" https://a.test)',
            f'Bash(curl -H "X-Api-Key: {s["letters"]}" https://a.test)',
            f'Bash(curl -H "Authorization: token {s["letters"]}" https://a.test)',
            f'Bash(API_KEY="{s["quoted"]}" curl https://a.test)',
            f"Bash(export SECRET='{s['quoted']}')",
            f'Bash(curl -u admin:{s["upass"]} https://a.test)',
            f'Bash(gh auth login --token {s["flag"]})',
            f'Bash(aws configure set aws_secret_access_key {s["aws"]})',
        ):
            text, kinds = lint.redact(rule)
            self.assert_clean(text)
            self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", text)
            self.assertTrue(kinds, rule)

    def test_redact_leaves_clean_text_alone(self):
        for clean in ("Bash(flutter test:*)", "WebFetch(domain:docs.test) bearer authentication",
                      "Bash(grep -r 'Authorization: placeholder' src)", "Basic authentication guide",
                      "Bash(gh auth login --token $GITHUB_TOKEN)", "Bash(curl -u $USER:$PASS https://a.test)",
                      "Bash(git clone https://github.com/acme/app.git)"):
            self.assertEqual(lint.redact(clean), (clean, []))

    def test_rules_never_leak(self):
        s = self.SECRETS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo = root / "home", root / "repo"
            git_repo(repo)
            write(repo / ".claude/settings.local.json", json.dumps({"permissions": {"allow": [
                f'Bash(curl "https://x.test/?key={s["google"]}" -H "token={s["github"]}")',
                f'Bash(cd /Users/nobody/gone && curl -H "Authorization: Bearer {s["bearer"]}" https://a.test)',
                f'Bash(curl -H "x-api-key: {s["header"]}" https://api.test/{"p" * 120})',
                f'Bash(git clone https://me:{s["urlpass"]}@git.test/r.git)',
            ]}}))
            result = lint.Linter(repo.resolve(), home.resolve()).run()
            out = json.dumps(result)
            self.assert_clean(out)
            self.assertEqual(sum(f["code"] == "perm-secret" for f in result["findings"]), 4, out)


class SafeFixes(unittest.TestCase):
    """Suggested commands must not destroy content or misbehave on macOS."""

    def run_lint(self, build) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo = root / "home", root / "repo"
            git_repo(repo)
            build(home, repo)
            return lint.Linter(repo.resolve(), home.resolve()).run()["findings"]

    def test_per_skill_dir_keeps_skills_that_live_only_there(self):
        def build(home, repo):
            skill(repo / ".agents/skills", "shared")
            (repo / ".claude/skills").mkdir(parents=True)
            os.symlink("../../.agents/skills/shared", repo / ".claude/skills/shared")
            skill(repo / ".claude/skills", "only-here")
        f = next(f for f in self.run_lint(build) if f["code"] == "link-repo-skills")
        self.assertIn("only in .claude/skills: only-here", f["message"])
        self.assertIn("only-here", f["fix"].split("&&")[0])
        self.assertNotIn("git rm", f["fix"])  # untracked folder

    def test_tracked_per_skill_dir_uses_git_rm(self):
        def build(home, repo):
            skill(repo / ".agents/skills", "shared")
            (repo / ".claude/skills").mkdir(parents=True)
            os.symlink("../../.agents/skills/shared", repo / ".claude/skills/shared")
            subprocess.run(["git", "-C", str(repo), "add", ".claude/skills"], check=True)
        f = next(f for f in self.run_lint(build) if f["code"] == "link-repo-skills")
        self.assertIn("git rm -r .claude/skills", f["fix"])
        self.assertNotIn("only in", f["message"])

    def test_no_ln_sfn_onto_a_real_directory(self):
        def build(home, repo):
            write(home / ".claude/settings.json", "{}")
            write(home / ".claude-acct2/settings.json", "{}")
            skill(home / ".claude-acct2/skills", "own-skill")  # a real folder, not a link
        f = next(f for f in self.run_lint(build) if f["code"] == "config-drift" and f["where"].endswith("/skills"))
        self.assertNotIn("ln -sfn", f["fix"])
        self.assertIn("rmdir", f["fix"])

    def test_non_ascii_paths(self):
        def build(home, repo):
            write(repo / "cafe-notes-\u00e9/AGENTS.md", "x")
        wheres = [f["where"] for f in self.run_lint(build) if f["code"] == "link-claude-md"]
        self.assertIn("cafe-notes-\u00e9/AGENTS.md", wheres)


class ReviewFixes(unittest.TestCase):
    """Issues the second review of phases 1-4 found."""

    run_lint = SafeFixes.run_lint

    def test_rule_matching_two_risks_is_one_finding(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({"permissions": {"allow": [
                "Bash(sudo unset HTTPS_PROXY)"]}}))
        risky = [f for f in self.run_lint(build) if f["code"] == "perm-risky"]
        self.assertEqual(len(risky), 1, risky)
        self.assertIn("proxy", risky[0]["message"])
        self.assertIn("privileged", risky[0]["message"])

    def test_broken_per_skill_link_counts_as_missing(self):
        def build(home, repo):
            skill(repo / ".agents/skills", "shared")
            (repo / ".claude/skills").mkdir(parents=True)
            os.symlink("../../.agents/skills/gone", repo / ".claude/skills/shared")
        f = next(f for f in self.run_lint(build) if f["code"] == "link-repo-skills")
        self.assertIn("missing for Claude: shared", f["message"])

    def test_dangling_account_links(self):
        def build(home, repo):
            write(home / ".claude/CLAUDE.md", "rules")
            (home / ".claude/skills").mkdir(parents=True)
            write(home / ".claude-acct2/settings.json", "{}")
            os.symlink("../nowhere/CLAUDE.md", home / ".claude-acct2/CLAUDE.md")
            os.symlink("../nowhere/skills", home / ".claude-acct2/skills")
        drift = {f["where"]: f for f in self.run_lint(build) if f["code"] == "config-drift"}
        for where, fix in (("~/.claude-acct2/CLAUDE.md", "ln -sf ../.claude/CLAUDE.md"),
                           ("~/.claude-acct2/skills", "ln -sfn ../.claude/skills")):
            self.assertIn("broken link", drift[where]["message"])
            self.assertIn(fix, drift[where]["fix"])

    def test_accepted_bare_rule_keys_still_match(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({"permissions": {"allow": [
                "Bash(cd /Users/nobody/gone && make)"]}}))
        stale = next(f for f in self.run_lint(build) if f["code"] == "perm-stale")
        code, where, key = stale["fingerprint"].split("|", 2)
        with tempfile.TemporaryDirectory() as tmp:
            accepted_file = Path(tmp) / "accepted.json"
            write(accepted_file, json.dumps({f"{code}|{where}|{key.split(':', 1)[1]}": "old"}))
            result = lint.apply_accepted({"findings": [stale]}, accepted_file)
            self.assertEqual(len(result["accepted"]), 1)

    def test_unknown_accept_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / "home", Path(tmp) / "repo"
            git_repo(repo)
            home.mkdir()
            script = Path(lint.__file__)
            run = subprocess.run([sys.executable, "-B", str(script), "--repo", str(repo), "--home", str(home),
                                  "--accept", "deadbeef", "--reason", "x"], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("accepted 0", run.stderr)


class PolicyConsistency(unittest.TestCase):
    """The linter agrees with policy.md and stays quiet where it has nothing to say."""

    run_lint = SafeFixes.run_lint

    def test_never_suggests_deleting_live_antigravity_config(self):
        def build(home, repo):
            skill(home / ".gemini/skills", "agy-skill")
            write(home / ".gemini/config/mcp_config.json", "{}")
            write(repo / "GEMINI.md", "x")
        findings = self.run_lint(build)
        self.assertFalse([f for f in findings if "~/.gemini" in f["where"] + f["fix"]], findings)
        gemini_md = next(f for f in findings if f["where"] == "GEMINI.md")
        self.assertNotIn("leftover", gemini_md["message"])

    def test_scratch_paths_are_not_dead_refs(self):
        def build(home, repo):
            skill(repo / ".agents/skills", "stager", body="Stage files in `/tmp/claude/run/staged/`, then "
                  "`/private/tmp/claude-501/x/scratchpad/out.json`.")
        self.assertFalse([f for f in self.run_lint(build) if f["code"] == "skill-dead-ref"])

    def test_one_warning_per_real_skills_folder(self):
        def build(home, repo):
            skill(home / ".agents/skills", "unlinked")
            (home / ".claude/skills").mkdir(parents=True)
            write(home / ".claude-acct2/settings.json", "{}")
            os.symlink("../.claude/skills", home / ".claude-acct2/skills")
        hits = [f for f in self.run_lint(build) if f["code"] == "link-global-skills"]
        self.assertEqual(len(hits), 1, hits)

    def test_account_missing_claude_md_and_skills(self):
        def build(home, repo):
            write(home / ".claude/CLAUDE.md", "global rules")
            (home / ".claude/skills").mkdir(parents=True)
            write(home / ".claude-acct2/settings.json", "{}")
        drift = {f["where"]: f for f in self.run_lint(build) if f["code"] == "config-drift"}
        self.assertIn("~/.claude-acct2/CLAUDE.md", drift)
        self.assertIn("~/.claude-acct2/skills", drift)
        self.assertIn("ln -s ../.claude/skills", drift["~/.claude-acct2/skills"]["fix"])

    def test_vendored_folders_are_not_searched(self):
        def build(home, repo):
            write(repo / "node_modules/pkg/docs/guide.md")
            write(repo / "AGENTS.md", "See `docs/guide.md`.")
            os.symlink("AGENTS.md", repo / "CLAUDE.md")
        refs = [f for f in self.run_lint(build) if f["code"] == "doc-refs"]
        self.assertEqual([f["message"] for f in refs], ["refers to `docs/guide.md`, which does not exist"])


class Settings(unittest.TestCase):
    """Loading rules from the Claude Code docs (settings.md, mcp.md)."""

    def lint(self, build) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo = root / "home", root / "repo"
            git_repo(repo)
            build(home, repo)
            return lint.Linter(repo.resolve(), home.resolve()).run()

    def test_connectors_true_from_any_file_wins(self):
        def build(home, repo):
            write(home / ".claude/settings.json", json.dumps({"disableClaudeAiConnectors": True}))
            write(repo / ".claude/settings.local.json", json.dumps({"disableClaudeAiConnectors": False}))
        result = self.lint(build)
        self.assertEqual(result["inventory"]["claude_ai_connectors"], "off")
        self.assertNotIn("mcp-connectors", {f["code"] for f in result["findings"]})

    def test_connectors_on_when_no_file_disables_them(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({"disableClaudeAiConnectors": False}))
        result = self.lint(build)
        self.assertEqual(result["inventory"]["claude_ai_connectors"], "on")
        self.assertIn("mcp-connectors", {f["code"] for f in result["findings"]})

    def test_committed_shared_settings(self):
        def build(home, repo):
            write(repo / ".claude/settings.json", json.dumps({"env": {}}))
            subprocess.run(["git", "-C", str(repo), "add", ".claude/settings.json"], check=True)
        fps = {f["fingerprint"] for f in self.lint(build)["findings"]}
        self.assertIn("hygiene|.claude/settings.json|committed", fps)

    def test_untracked_shared_settings_is_not_committed(self):
        def build(home, repo):
            write(repo / ".claude/settings.json", json.dumps({"env": {}}))
        fps = {f["fingerprint"] for f in self.lint(build)["findings"]}
        self.assertNotIn("hygiene|.claude/settings.json|committed", fps)


class RunsDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = Path("/work/app")
        self.legacy = self.home / ".agents/lint-runs/app"
        write(self.legacy / "20261031-182729.json", json.dumps({"repo": str(self.repo), "findings": []}))
        write(self.legacy / "accepted.json", "{}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_name_repos_get_separate_dirs(self):
        a = lint.runs_dir_for(self.home, Path("/work/app"), write=False)
        b = lint.runs_dir_for(self.home, Path("/other/app"), write=False)
        self.assertNotEqual(a, b)

    def test_read_only_run_uses_legacy_dir_without_moving_it(self):
        self.assertEqual(lint.runs_dir_for(self.home, self.repo, write=False), self.legacy)
        self.assertTrue(self.legacy.is_dir())

    def test_write_migrates_legacy_dir(self):
        new = lint.runs_dir_for(self.home, self.repo, write=True)
        self.assertNotEqual(new, self.legacy)
        self.assertFalse(self.legacy.exists())
        self.assertTrue((new / "accepted.json").exists())

    def test_legacy_dir_without_runs_is_not_adopted(self):
        (self.legacy / "20261031-182729.json").unlink()
        new = lint.runs_dir_for(self.home, self.repo, write=True)
        self.assertNotEqual(new, self.legacy)
        self.assertTrue(self.legacy.is_dir())

    def test_legacy_dir_of_another_repo_is_left_alone(self):
        new = lint.runs_dir_for(self.home, Path("/other/app"), write=True)
        self.assertNotEqual(new, self.legacy)
        self.assertTrue(self.legacy.is_dir())


class RunHistory(unittest.TestCase):
    def test_accepted_file_is_not_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            old = {"findings": [{"fingerprint": "a|x|1"}, {"fingerprint": "b|x|2"}]}
            write(runs / "20261031-182729.json", json.dumps(old))
            write(runs / "accepted.json", json.dumps({"c|x|3": "kept"}))
            result = {"findings": [{"fingerprint": "a|x|1"}, {"fingerprint": "d|x|4"}]}
            result = lint.compare_with_previous(result, runs)
            self.assertEqual(result["previous_run"], "20261031-182729.json")
            self.assertEqual([f["status"] for f in result["findings"]], ["persisting", "new"])
            self.assertEqual([f["fingerprint"] for f in result["resolved"]], ["b|x|2"])

    def test_latest_run_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            write(runs / "20261031-182729.json", json.dumps({"findings": []}))
            write(runs / "20261101-090000.json", json.dumps({"findings": [{"fingerprint": "a|x|1"}]}))
            result = lint.compare_with_previous({"findings": [{"fingerprint": "a|x|1"}]}, runs)
            self.assertEqual(result["previous_run"], "20261101-090000.json")
            self.assertEqual(result["findings"][0]["status"], "persisting")


CTX7 = "ctx7sk-0a1b2c3d-4e5f-6789-abcd-ef0123456789"
BEARER = "Bearer abcdefGHIJKL0123456789"


class Coverage(unittest.TestCase):
    """Phase 5: extra accounts, Codex, Antigravity, risky settings, hooks, the rest of the inventory."""

    scan = Settings.lint

    def assert_no_leak(self, result, *secrets):
        out = json.dumps(result)
        for s in secrets:
            self.assertNotIn(s, out)

    def fps(self, result):
        return {f["fingerprint"]: f for f in result["findings"]}

    def test_extra_account_permissions_mcp_memory_and_leaks(self):
        def build(home, repo):
            write(home / ".claude-acct2/settings.json", json.dumps({
                "disableClaudeAiConnectors": True,
                "autoMode": {"environment": ["Trusted repo: acme/old_name"]},
                "permissions": {"allow": ["Bash(curl:*)"]}}))
            write(home / ".claude-acct2/.claude.json", json.dumps({
                "mcpServers": {"docs": {"type": "http", "url": "https://d.test", "headers": {"X-Key": "${DOCS_KEY}"}}},
                "projects": {str(repo.resolve()): {"mcpServers": {"local": {"command": "srv"}}}}}))
            mem = home / ".claude-acct2/projects" / lint.re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve())) / "memory"
            write(mem / "MEMORY.md", "- [Gone](gone.md) — x\n")
        result = self.scan(build)
        fps = self.fps(result)
        self.assertIn("perm-risky|~/.claude-acct2/settings.json|Bash(curl:*)", fps)
        self.assertIn("global-leak|~/.claude-acct2/settings.json|acme/old_name", fps)
        self.assertTrue(any(k.startswith("memory|~/.claude-acct2/memory/gone.md") for k in fps), fps)
        servers = {(s["name"], s["source"]) for s in result["inventory"]["mcp_servers"]}
        self.assertIn(("docs", "~/.claude-acct2/.claude.json"), servers)
        self.assertIn(("local", "~/.claude-acct2/.claude.json (project)"), servers)
        self.assertNotIn("perm-secret", {f["code"] for f in result["findings"]})  # ${VAR} is not a secret

    def test_mcp_header_secret_by_key_name(self):
        def build(home, repo):
            write(home / ".claude.json", json.dumps({"mcpServers": {"context7": {
                "type": "http", "url": "https://c.test", "headers": {"CONTEXT7_API_KEY": "plainvalue-" + "x" * 20}}}}))
            write(repo / ".mcp.json", json.dumps({"mcpServers": {"svc": {
                "command": "svc", "env": {"SVC_TOKEN": CTX7, "LOG_LEVEL": "debug"}}}}))
        result = self.scan(build)
        fps = self.fps(result)
        home_f = fps["perm-secret|~/.claude.json:context7|inline-secret"]
        self.assertEqual(home_f["severity"], "warn")
        self.assertIn("CONTEXT7_API_KEY", home_f["message"])
        repo_f = fps["perm-secret|.mcp.json:svc|inline-secret"]
        self.assertEqual(repo_f["severity"], "error")
        self.assertNotIn("LOG_LEVEL", repo_f["message"])
        self.assert_no_leak(result, "plainvalue-", CTX7)

    @unittest.skipUnless(lint.tomllib, "tomllib needs Python 3.11+")
    def test_codex_config(self):
        def build(home, repo):
            write(home / ".codex/config.toml", "\n".join([
                'sandbox_mode = "danger-full-access"', 'approval_policy = "never"',
                "[mcp_servers.context7]", 'url = "https://c.test"',
                "[mcp_servers.context7.http_headers]", f'CONTEXT7_API_KEY = "{CTX7}"',
                "[mcp_servers.dart]", 'command = "/x/flutter/versions/3.1.0/bin/dart"',
                f'[projects."{repo.resolve()}"]', 'trust_level = "trusted"', ""]))
        result = self.scan(build)
        fps = self.fps(result)
        self.assertEqual(fps["perm-secret|~/.codex/config.toml:context7|inline-secret"]["severity"], "warn")
        self.assertIn("mcp-unpinned|~/.codex/config.toml:dart|/x/flutter/versions/3.1.0/bin/dart", fps)
        risky = fps["codex-risky|~/.codex/config.toml|"]
        self.assertIn("danger-full-access", risky["message"])
        self.assertIn("never", risky["message"])
        servers = {(s["agent"], s["name"]) for s in result["inventory"]["mcp_servers"]}
        self.assertIn(("codex", "context7"), servers)
        self.assertEqual(result["inventory"]["codex"]["trust_level"], "trusted")
        self.assert_no_leak(result, CTX7)

    def test_codex_without_tomllib_is_an_info(self):
        def build(home, repo):
            write(home / ".codex/config.toml", 'sandbox_mode = "danger-full-access"\n')
        saved, lint.tomllib = lint.tomllib, None
        try:
            result = self.scan(build)
        finally:
            lint.tomllib = saved
        f = next(f for f in result["findings"] if f["code"] == "codex-config")
        self.assertEqual(f["severity"], "info")

    def test_antigravity_mcp_and_hooks(self):
        def build(home, repo):
            write(home / ".gemini/config/mcp_config.json", json.dumps({"mcpServers": {
                "context7": {"serverUrl": "https://c.test", "headers": {"Authorization": BEARER}},
                "dart-mcp-server": {"command": "dart", "args": ["mcp-server"]}}}))
            write(home / ".gemini/config/hooks/state.sh")
            write(home / ".gemini/config/hooks.json", json.dumps({"tracker": {"PreInvocation": [
                {"type": "command", "command": f"bash {home}/.gemini/config/hooks/state.sh", "timeout": 5}]}}))
        result = self.scan(build)
        fps = self.fps(result)
        self.assertIn("perm-secret|~/.gemini/config/mcp_config.json:context7|inline-secret", fps)
        servers = {(s["agent"], s["name"]) for s in result["inventory"]["mcp_servers"]}
        self.assertIn(("antigravity", "dart-mcp-server"), servers)
        hooks = result["inventory"]["hooks"]
        self.assertEqual([(h["agent"], h["event"]) for h in hooks], [("antigravity", "tracker/PreInvocation")])
        self.assertNotIn("hook-missing", {f["code"] for f in result["findings"]})
        self.assert_no_leak(result, BEARER.split()[1])

    def test_risky_settings(self):
        def build(home, repo):
            write(home / ".claude/settings.json", json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))
            write(repo / ".claude/settings.json", json.dumps({"enableAllProjectMcpServers": True}))
            write(repo / ".claude/settings.local.json", json.dumps({
                "enableAllProjectMcpServers": True,
                "sandbox": {"enabled": True, "excludedCommands": ["docker", "gh"]},
                "permissions": {"allow": ["Bash(npx:*)", "Bash(python3:*)", "Bash(node *)", "Bash(npx prettier:*)"]}}))
        fps = self.fps(self.scan(build))
        self.assertEqual(fps["perm-mode|~/.claude/settings.json|bypass"]["severity"], "warn")
        self.assertEqual(fps["mcp-autoenable|.claude/settings.json|"]["severity"], "warn")
        self.assertNotIn("mcp-autoenable|.claude/settings.local.json|", fps)  # cut in round 2: a deliberate local choice
        for rule in ("Bash(npx:*)", "Bash(python3:*)", "Bash(node *)"):
            self.assertIn(f"perm-risky|.claude/settings.local.json|{rule}", fps)
        self.assertNotIn("perm-risky|.claude/settings.local.json|Bash(npx prettier:*)", fps)

    def test_hooks_are_listed_redacted_and_missing_scripts_flagged(self):
        token = "ghp_" + "A1b2C3d4" * 5
        def build(home, repo):
            write(home / ".claude/hooks/ok.sh")
            write(home / ".claude/settings.json", json.dumps({"hooks": {
                "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "bash ~/.claude/hooks/ok.sh"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [
                    {"type": "command", "command": f"GITHUB_TOKEN={token} ~/.claude/hooks/gone.py"}]}]}}))
            write(home / ".codex/hooks.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [
                {"type": "command", "command": "$HOME/.codex/hooks/missing.sh"}]}]}}))
            write(repo / ".claude/settings.local.json", json.dumps({"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/stop.sh"}]}]}}))
        result = self.scan(build)
        hooks = {(h["agent"], h["source"], h["event"]) for h in result["inventory"]["hooks"]}
        self.assertEqual(hooks, {
            ("claude", "~/.claude/settings.json", "SessionStart"), ("claude", "~/.claude/settings.json", "PreToolUse"),
            ("codex", "~/.codex/hooks.json", "SessionStart"), ("claude", ".claude/settings.local.json", "Stop")})
        missing = sorted(f["message"] for f in result["findings"] if f["code"] == "hook-missing")
        self.assertEqual(len(missing), 3, missing)
        self.assertTrue(any("gone.py" in m for m in missing))
        self.assertTrue(any("missing.sh" in m for m in missing))
        self.assertTrue(any("scripts/stop.sh" in m for m in missing))
        self.assert_no_leak(result, token)

    def test_inventory_of_rules_agents_commands_imports_plugins(self):
        def build(home, repo):
            write(repo / "AGENTS.md", "Style: @docs/style.md and @docs/gone.md.\nMail a@b.com, see `@not/an-import.md`,\n"
                                      "uses @anthropic-ai/sdk.\n```\n@also/not.md\n```\n")
            os.symlink("AGENTS.md", repo / "CLAUDE.md")
            write(repo / "docs/style.md", "x")
            write(repo / "CLAUDE.local.md", "personal notes")
            write(repo / ".claude/rules/api.md", "---\npaths: src/api/**\n---\nAPI rules")
            write(repo / ".claude/rules/always.md", "always loaded")
            write(repo / ".claude/agents/reviewer.md", "---\nname: reviewer\n---\n")
            write(repo / ".claude/commands/ship.md", "ship it")
            write(home / ".claude/agents/helper.md", "x")
            write(home / ".claude/settings.json", json.dumps({"enabledPlugins": {"a@m": True, "b@m": False}}))
        result = self.scan(build)
        inv = result["inventory"]
        self.assertEqual(inv["claude_rules"], [".claude/rules/always.md", ".claude/rules/api.md"])
        self.assertIn("CLAUDE.local.md", inv["instruction_files_chars"])
        self.assertIn(".claude/rules/always.md", inv["instruction_files_chars"])
        self.assertNotIn(".claude/rules/api.md", inv["instruction_files_chars"])  # loads only for matching paths
        self.assertEqual(inv["agents"], [".claude/agents/reviewer.md", "~/.claude/agents/helper.md"])
        self.assertEqual(inv["commands"], [".claude/commands/ship.md"])
        self.assertEqual(inv["plugins"], ["a@m"])
        self.assertEqual(sorted(i["import"] for i in inv["imports"]), ["docs/gone.md", "docs/style.md"])
        fps = self.fps(result)
        self.assertEqual(fps["doc-import|AGENTS.md|docs/gone.md"]["severity"], "warn")
        self.assertEqual(fps["doc-import|AGENTS.md|docs/style.md"]["severity"], "info")  # Claude-only


class Round2(unittest.TestCase):
    """Second scoring review, re-read for the cleanup goal: agent leftovers, not security-scanner depth."""

    scan = Settings.lint
    fps = Coverage.fps

    # -- A: mistakes agents leave -------------------------------------------
    def test_env_assignment_keys_are_redacted(self):
        for text in ("Bash(SUPABASE_SERVICE_ROLE_KEY=abc123def456 npm run seed)", "PGPASSWORD=hunter22 psql",
                     "export STRIPE_SECRET=xyz789abc0"):
            clean, kinds = lint.redact(text)
            self.assertTrue(kinds, text)
            self.assertNotRegex(clean, r"abc123def456|hunter22|xyz789abc0")
        for text in ("PGPASSWORD=$PGPASS psql", "API_KEY=${KEY}"):
            self.assertEqual(lint.redact(text)[1], [], text)

    def test_keys_in_settings_env_and_hooks(self):
        def build(home, repo):
            write(home / ".claude/settings.json", json.dumps({
                "env": {"OPENAI_API_KEY": "plain-value-123456", "LOG_LEVEL": "debug", "GH_TOKEN": "${GH}"},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "curl -H 'Authorization: Bearer Zx9Yw8Vu7Ts6Rq5' x.test"}]}]}}))
        result = self.scan(build)
        fps = self.fps(result)
        self.assertIn("OPENAI_API_KEY", fps["perm-secret|~/.claude/settings.json|env:OPENAI_API_KEY"]["message"])
        self.assertNotIn("perm-secret|~/.claude/settings.json|env:GH_TOKEN", fps)
        self.assertIn("perm-secret|~/.claude/settings.json|hook:Stop", fps)
        self.assertNotIn("plain-value-123456", json.dumps(result))
        self.assertNotIn("Zx9Yw8Vu7Ts6Rq5", json.dumps(result))

    def test_invalid_config_is_a_finding_not_empty(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", '{"permissions": {"deny": ["x"],}}')
            write(home / ".claude/settings.json", "[1, 2]")
        fps = self.fps(self.scan(build))
        f = fps["config-invalid|.claude/settings.local.json|"]
        self.assertEqual(f["severity"], "error")
        self.assertIn("line 1", f["message"])
        self.assertNotIn("deny", f["message"])
        self.assertIn("config-invalid|~/.claude/settings.json|", fps)

    def test_odd_types_do_not_crash(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({
                "permissions": {"allow": "Bash(ls)", "deny": [3, None]}, "hooks": [], "sandbox": "on", "env": [1]}))
        self.scan(build)

    def test_skills_dir_fix_never_deletes_work(self):
        def build(home, repo):
            skill(repo / ".agents/skills", "same")
            skill(repo / ".agents/skills", "changed", body="new")
            skill(repo / ".claude/skills", "same")
            skill(repo / ".claude/skills", "changed", body="old local edit")
            write(repo / ".claude/skills/notes.txt", "x")
        f = next(f for f in self.scan(build)["findings"] if f["code"] == "link-repo-skills")
        self.assertIn("differs from .agents/skills: changed", f["message"])
        self.assertIn("not a skill: notes.txt", f["message"])
        self.assertNotIn("rm -r", f["fix"])
        self.assertNotIn("same", f["message"].split("differs")[1])

    # -- B: correct inventory, fewer false alarms -----------------------------
    def test_mcpjson_approval_in_claude_json(self):
        def build(home, repo):
            write(repo / ".mcp.json", json.dumps({"mcpServers": {"firebase": {"command": "npx"}}}))
            write(home / ".claude.json", json.dumps({"projects": {str(repo.resolve()): {"enabledMcpjsonServers": ["firebase"]}}}))
        result = self.scan(build)
        self.assertTrue(next(s for s in result["inventory"]["mcp_servers"] if s["name"] == "firebase")["enabled"])
        self.assertIn("mcp-destructive", {f["code"] for f in result["findings"]})

    def test_server_level_and_wildcard_denies(self):
        for deny in ("mcp__firebase", "mcp__firebase__*"):
            def build(home, repo, deny=deny):
                write(repo / ".mcp.json", json.dumps({"mcpServers": {"firebase": {"command": "npx"}}}))
                write(repo / ".claude/settings.local.json", json.dumps({
                    "enabledMcpjsonServers": ["firebase"], "permissions": {"deny": [deny]}}))
            self.assertNotIn("mcp-destructive", {f["code"] for f in self.scan(build)["findings"]}, deny)

    def test_plugin_and_builtin_servers_are_not_stale(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({"permissions": {
                "allow": ["mcp__plugin_cf_cloudflare__search", "mcp__claude-in-chrome__navigate"],
                "deny": ["mcp__gone__drop_table"]}}))
        stale = [f for f in self.scan(build)["findings"] if f["code"] == "perm-stale"]
        self.assertEqual([f["fingerprint"] for f in stale], ["perm-stale|.claude/settings.local.json|mcp:mcp__gone__drop_table"])
        self.assertIn("add it back", stale[0]["fix"])

    def test_bearer_var_reference_is_not_a_secret(self):
        def build(home, repo):
            write(repo / ".mcp.json", json.dumps({"mcpServers": {"api": {
                "type": "http", "url": "https://a.test", "headers": {"Authorization": "Bearer ${API_TOKEN}"}}}}))
        self.assertNotIn("perm-secret", {f["code"] for f in self.scan(build)["findings"]})

    def test_sandbox_enabled_read_across_layers(self):
        def build(home, repo):
            write(home / ".claude/settings.json", json.dumps({"sandbox": {"enabled": True}}))
            write(repo / ".claude/settings.local.json", json.dumps({"sandbox": {"excludedCommands": ["docker"]}}))
        fps = self.fps(self.scan(build))
        self.assertNotIn("hygiene|.claude/settings.local.json|sandbox", fps)
        self.assertNotIn("sandbox-excluded", {f["code"] for f in fps.values()})

    def test_other_repo_slug_is_info_and_home_paths_ignored(self):
        def build(home, repo):
            write(home / ".claude/settings.json", json.dumps({
                "autoMode": {"environment": ["Trusted repos: acme/app, acme/other"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/Users/acme/bin/x"}]}]}}))
        leaks = {f["fingerprint"]: f["severity"] for f in self.scan(build)["findings"] if f["code"] == "global-leak"}
        self.assertEqual(leaks["global-leak|~/.claude/settings.json|acme/other"], "info")
        self.assertNotIn("global-leak|~/.claude/settings.json|acme/bin", leaks)

    # -- C/D: inventory and new clutter checks -----------------------------------
    def test_plugin_skills_and_servers(self):
        def build(home, repo):
            plugin = home / ".claude/plugins/cache/mk/cf/1.0.0"
            skill(plugin / "skills", "workers", description="Cloudflare Workers.")
            write(plugin / ".mcp.json", json.dumps({"mcpServers": {"cloudflare": {"type": "http", "url": "https://c.test"}}}))
            write(home / ".claude/plugins/installed_plugins.json", json.dumps({"version": 2, "plugins": {
                "cf@mk": [{"scope": "local", "projectPath": str(repo.resolve()), "installPath": str(plugin)}]}}))
            write(repo / ".claude/settings.local.json", json.dumps({"enabledPlugins": {"cf@mk": True}}))
        inv = self.scan(build)["inventory"]
        self.assertIn("cf:workers", [s["name"] for s in inv["skills"]])
        self.assertIn(("plugin_cf_cloudflare", "plugin cf@mk"), [(s["name"], s["source"]) for s in inv["mcp_servers"]])

    def test_hidden_skills_listed_with_state(self):
        def build(home, repo):
            for n in ("on-skill", "off-skill", "manual"):
                skill(home / ".agents/skills", n)
                (home / ".claude/skills").mkdir(parents=True, exist_ok=True)
                os.symlink(f"../../.agents/skills/{n}", home / ".claude/skills" / n)
            write(home / ".agents/skills/manual/SKILL.md", "---\nname: manual\ndescription: x\ndisable-model-invocation: true\n---\n")
            write(repo / ".claude/settings.local.json", json.dumps({"skillOverrides": {"off-skill": "off", "deleted": "off"}}))
        result = self.scan(build)
        inv = result["inventory"]
        states = {s["name"]: s["state"] for s in inv["skills"]}
        self.assertEqual(states, {"on-skill": "listed", "off-skill": "off", "manual": "user-only"})
        self.assertEqual(inv["skills_listed"], 1)
        self.assertIn("stale-name|.claude/settings.local.json|skillOverrides:deleted", self.fps(result))

    def test_stale_mcpjson_names_and_duplicate_servers(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({"enabledMcpjsonServers": ["gone"]}))
            write(home / ".claude.json", json.dumps({"mcpServers": {"context7": {"type": "http", "url": "https://c.test"}}}))
            write(home / ".gemini/config/mcp_config.json", json.dumps({"mcpServers": {"context7": {"serverUrl": "https://c.test"}}}))
        fps = self.fps(self.scan(build))
        self.assertIn("stale-name|.claude/settings.local.json|enabledMcpjsonServers:gone", fps)
        dup = fps["mcp-duplicate|context7|"]
        self.assertEqual(dup["severity"], "info")
        self.assertIn("~/.gemini/config/mcp_config.json", dup["message"])

    def test_memory_index_empty_or_too_long(self):
        def build(home, repo):
            mem = home / ".claude/projects" / lint.re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve())) / "memory"
            write(mem / "MEMORY.md", "")
        self.assertIn("memory|memory/MEMORY.md|empty", self.fps(self.scan(build)))
        def build_long(home, repo):
            mem = home / ".claude/projects" / lint.re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve())) / "memory"
            write(mem / "MEMORY.md", "".join(f"- line {i}\n" for i in range(190)))
        self.assertEqual(self.fps(self.scan(build_long))["memory|memory/MEMORY.md|long"]["severity"], "warn")

    def test_security_creep_removed(self):
        def build(home, repo):
            write(home / ".claude/settings.json", json.dumps({"enableAllProjectMcpServers": True}))
            write(repo / ".claude/settings.local.json", json.dumps({
                "enableAllProjectMcpServers": True, "sandbox": {"enabled": True, "excludedCommands": ["docker"]},
                "permissions": {"allow": ["Bash(curl -H 'Authorization: Bearer Zx9Yw8Vu7Ts6Rq5' x)"]}}))
        findings = self.scan(build)["findings"]
        codes = {f["code"] for f in findings}
        self.assertNotIn("sandbox-excluded", codes)
        self.assertNotIn("mcp-autoenable", codes)
        self.assertFalse(any("rotate" in f["fix"] for f in findings))

    # -- F: CLI ----------------------------------------------------------------
    def test_cli_checks_reason_and_repo_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            legacy = home / ".agents/lint-runs/app"
            write(legacy / "20260101-000000.json", json.dumps({"repo": str((Path(tmp) / "app").resolve()), "findings": []}))
            git_repo(Path(tmp) / "app")
            script = str(Path(lint.__file__))
            r = subprocess.run([sys.executable, script, "--repo", str(Path(tmp) / "app"), "--home", str(home),
                                "--accept", "abc"], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(legacy.exists(), "legacy dir moved before --reason was checked")
            r = subprocess.run([sys.executable, script, "--repo", str(Path(tmp) / "nope"), "--home", str(home)],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


class Defaults(unittest.TestCase):
    """What a stranger gets with no config file: no opinions about their own choices."""

    scan = Settings.lint
    fps = Coverage.fps

    def setUp(self):
        self.saved, lint.DEFAULTS = lint.DEFAULTS, dict(PUBLIC_DEFAULTS)

    def tearDown(self):
        lint.DEFAULTS = self.saved

    def test_personal_choices_are_not_findings(self):
        def build(home, repo):
            write(repo / "CLAUDE.md", "only Claude here")                        # no AGENTS.md beside it
            write(repo / "GEMINI.md", "x")
            write(repo / ".gemini/settings.json", "{}")
            write(repo / ".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
            write(repo / "pkg/AGENTS.md", "x")
            subprocess.run(["git", "-C", str(repo), "add", ".claude/settings.json"], check=True)
            skill(repo / ".claude/skills", "own-skill")                          # Claude-only skill folder
        codes = {f["code"] for f in self.scan(build)["findings"]}
        for code in ("gemini-cli", "mcp-connectors", "perm-scope", "skill-misplaced"):
            self.assertNotIn(code, codes)
        fps = self.fps(self.scan(build))
        self.assertNotIn("hygiene|.claude/settings.json|committed", fps)
        self.assertNotIn("link-claude-md|CLAUDE.md|real-file", fps)
        self.assertIn("link-claude-md|pkg/AGENTS.md|no-link", fps)  # Claude never reads that AGENTS.md

    def test_two_real_instruction_files_drift(self):
        def build(home, repo):
            write(repo / "CLAUDE.md", "a")
            write(repo / "AGENTS.md", "b")
        self.assertEqual(self.fps(self.scan(build))["link-claude-md|CLAUDE.md|real-file"]["severity"], "warn")

    def test_config_file_turns_rules_on(self):
        def build(home, repo):
            write(home / ".agents/agentic-repo-lint.json", json.dumps({
                "agents": ["claude"], "claude_ai_connectors": "off",
                "destructive_tools": {"db": ["drop_table"]}, "unknown_key": 1}))
            write(repo / "GEMINI.md", "x")
            write(repo / ".mcp.json", json.dumps({"mcpServers": {"db": {"command": "db-mcp"}}}))
            write(repo / ".claude/settings.local.json", json.dumps({"enabledMcpjsonServers": ["db"]}))
        result = self.scan(build)
        codes = {f["code"] for f in result["findings"]}
        self.assertTrue({"gemini-cli", "mcp-connectors", "mcp-destructive"} <= codes, codes)
        self.assertEqual(result["inventory"]["config"]["file"], "~/.agents/agentic-repo-lint.json")
        self.assertNotIn("unknown_key", result["inventory"]["config"])


class ReleaseFixes(unittest.TestCase):
    """Bugs found in the release review."""

    scan = Settings.lint
    fps = Coverage.fps

    def test_claude_md_importing_agents_md_is_one_source(self):
        def build(home, repo):
            write(repo / "AGENTS.md", "rules")
            write(repo / "CLAUDE.md", "@AGENTS.md\n\nClaude-only: use the Skill tool.\n")
            write(repo / "pkg/AGENTS.md", "x")
            write(repo / "pkg/CLAUDE.md", "own copy of the rules")
        fps = self.fps(self.scan(build))  # strict profile
        self.assertNotIn("link-claude-md|CLAUDE.md|real-file", fps)
        self.assertIn("link-claude-md|pkg/CLAUDE.md|real-file", fps)
        self.assertIn("@AGENTS.md", fps["link-claude-md|pkg/CLAUDE.md|real-file"]["fix"])

    def test_file_name_values_are_not_keys(self):
        self.assertEqual(lint.redact("Bash(SIGNING_KEY=release.jks flutter build)")[1], [])
        self.assertFalse(lint.CRED_KEY.search("KEYBOARD_LAYOUT"))
        self.assertTrue(lint.CRED_KEY.search("API_KEY") and lint.CRED_KEY.search("X-Key") and lint.CRED_KEY.search("APIKEY"))

    def test_broken_link_inside_a_skill_copy_does_not_crash(self):
        def build(home, repo):
            skill(repo / ".agents/skills", "s")
            skill(repo / ".claude/skills", "s")
            os.symlink("nowhere", repo / ".claude/skills/s/dead")
        f = next(f for f in self.scan(build)["findings"] if f["code"] == "link-repo-skills")
        self.assertIn("differs from .agents/skills: s", f["message"])

    def test_linux_home_paths_are_checked(self):
        def build(home, repo):
            write(repo / ".claude/settings.local.json", json.dumps({"permissions": {"allow": ["Bash(cd /home/nobody/gone && make)"]}}))
        self.assertTrue(any("/home/nobody/gone" in f["message"] for f in self.scan(build)["findings"]))


class CleanRepo(unittest.TestCase):
    """The layout the decisions ask for produces no findings above info."""

    def test_clean_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo = root / "home", root / "repo"
            git_repo(repo)
            write(repo / "AGENTS.md", "Docs: `docs/a.md`")
            write(repo / "docs/a.md")
            os.symlink("AGENTS.md", repo / "CLAUDE.md")
            skill(repo / ".agents/skills", "app-qa",               # links the skill tells you to write elsewhere
                  body="Add `see [rules](./src/packages/README.md)`.\n```md\n[x](docs/new.md)\n```")
            (repo / ".claude").mkdir()
            os.symlink("../.agents/skills", repo / ".claude/skills")
            feature = repo / "lib/billing/backend"                    # a self-contained feature folder
            write(feature / "AGENTS.md", "Billing data.")
            os.symlink("AGENTS.md", feature / "CLAUDE.md")
            write(feature / "scripts/build.py")
            write(feature / "data/live.json")
            skill(feature / ".agents/skills", "billing-creator", body="Run `scripts/build.py`, write `data/new.json`.")
            (feature / ".claude").mkdir()
            os.symlink("../.agents/skills", feature / ".claude/skills")
            write(repo / ".mcp.json", json.dumps({"mcpServers": {
                "firebase": {"command": "npx", "args": ["-y", "firebase-tools@15.34.1", "mcp"]}}}))
            write(repo / ".claude/settings.local.json", json.dumps({
                "disableClaudeAiConnectors": True, "enabledMcpjsonServers": ["firebase"],
                "sandbox": {"enabled": True}, "permissions": {
                    "allow": ["Bash(flutter test:*)"],
                    "deny": [f"mcp__firebase__{t}" for t in lint.DESTRUCTIVE_TOOLS["firebase"]]}}))
            skill(home / ".agents/skills", "tdd")
            (home / ".claude/skills").mkdir(parents=True)
            os.symlink("../../.agents/skills/tdd", home / ".claude/skills/tdd")
            write(home / ".claude/settings.json", json.dumps({"disableClaudeAiConnectors": True}))

            result = lint.Linter(repo.resolve(), home.resolve()).run()
            loud = [f for f in result["findings"] if f["severity"] != "info"]
            self.assertEqual(loud, [], json.dumps(loud, indent=2))
            self.assertEqual(result["inventory"]["skills_listed"], 2)


if __name__ == "__main__":
    unittest.main()
