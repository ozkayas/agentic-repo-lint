#!/usr/bin/env python3
"""Mechanical checks for the agentic-repo-lint skill.

Inspects what coding agents (Claude Code, Codex, Antigravity) see in one repo
plus the user's global agent config, and prints findings as JSON or text.
Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11, e.g. macOS /usr/bin/python3 3.9: the Codex config is skipped
    tomllib = None

# Defaults for strangers; a user tightens them in ~/.agents/agentic-repo-lint.json (see references/policy.md).
DEFAULTS = {
    # Agents in use. A repo's .gemini/ and GEMINI.md are leftovers when "gemini-cli" is not listed.
    "agents": ["claude", "codex", "antigravity", "gemini-cli"],
    # "auto": flag CLAUDE.md/AGENTS.md drift only where both exist. "agents-md": AGENTS.md is canonical and
    # every CLAUDE.md must be a symlink to the AGENTS.md beside it.
    "instructions": "auto",
    # "any", or "off": claude.ai connectors must be disabled (`disableClaudeAiConnectors`).
    "claude_ai_connectors": "any",
    # "any", or "local": every permission rule lives in .claude/settings.local.json and a committed
    # .claude/settings.json is a finding (Claude reads the shared file only from the start directory).
    "permission_rules": "any",
    # MCP tools that need a deny rule while their server is enabled, by server name; merged with the user's.
    "destructive_tools": {},
    # Report language; null means the language the user writes in.
    "language": None,
}
CONFIG_FILE = ".agents/agentic-repo-lint.json"

# Known destructive MCP tools that need a deny rule while their server is enabled (extend in the config).
DESTRUCTIVE_TOOLS = {
    "firebase": [
        "firestore_delete_database",
        "firestore_delete_document",
        "firestore_delete_index",
        "firestore_delete_backup",
        "firestore_delete_backup_schedule",
        "firestore_update_database",
        "firebase_create_project",
    ],
}

# Specific token shapes first, then context patterns. A pattern with a `secret` group redacts only that
# group, so the context (`Bearer`, `token=`) stays readable; `<` is excluded so a redaction is not re-matched.
# Keywords are case-insensitive through scoped `(?i:...)` only: a global (?i) would make `[^a-z]` reject
# upper case too. A context value must hold something besides lower-case letters, so prose such as
# "bearer authentication" is not a secret, and a `$VAR` reference is never one.
_VALUE = r"(?!\$)(?=[^\s\"'<)]*[^a-z\s\"'<)])[^\s\"'<)]{12,}"
SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*)", re.S)),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("Anthropic key", re.compile(r"sk-ant-[0-9A-Za-z_-]{20,}")),
    ("Context7 key", re.compile(r"\bctx7sk-[0-9A-Za-z-]{20,}")),
    ("Stripe key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[0-9A-Za-z_-]{20,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{30,}")),
    ("Slack token", re.compile(r"\bxox[abpr]-[0-9A-Za-z-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("npm token", re.compile(r"\bnpm_[0-9A-Za-z]{30,}")),
    ("PyPI token", re.compile(r"\bpypi-[0-9A-Za-z_-]{30,}")),
    ("JWT", re.compile(r"\beyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}")),
    ("bearer token", re.compile(r"\b(?i:bearer)\s+(?P<secret>" + _VALUE + ")")),
    ("basic auth", re.compile(r"\b(?i:basic)\s+(?P<secret>(?=[0-9A-Za-z+/=]*[^a-z\s])[0-9A-Za-z+/=]{12,})")),
    ("token header", re.compile(r"\b(?i:authorization)\s*:\s*(?i:token)\s+(?P<secret>(?!\$)[^\s\"'<)]{8,})")),
    ("credential header", re.compile(
        r"\b(?i:x-api-key|api-key|x-auth-token|authorization)\s*:\s*(?!(?i:bearer|basic|token)\b)"
        r"(?P<secret>" + _VALUE + ")")),
    ("URL password", re.compile(r"://[^/\s:@<$]+:(?P<secret>(?!\$)[^@\s/<]{3,})@")),
    ("user:password flag", re.compile(r"(?:^|[\s(])(?:-u|--user)[\s=]+[\"']?(?!\$)[^\s:\"'$]+:(?P<secret>(?!\$)[^\s\"'@)<]{3,})")),
    ("credential flag", re.compile(
        r"(?:--(?i:token|password|passwd|api-?key|secret|access-?key|auth-?token)|(?i:aws_secret_access_key))"
        r"[\s=]+[\"']?(?P<secret>(?!\$)[^\s\"'<)]{8,})")),
    ("inline credential", re.compile(r"(?i:api_?key|token|secret|password)=[\"']?(?P<secret>(?!\$)[^\s\"'&)<]{12,})")),
    # A key pasted into a command: `PGPASSWORD=…`, `SUPABASE_SERVICE_ROLE_KEY=…`.
    # A file name (`SIGNING_KEY=release.jks`) is not a key.
    ("env assignment", re.compile(r"\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)=[\"']?"
                                  r"(?P<secret>(?![$/~])(?![\w-]+\.[A-Za-z]{2,8}(?![\w.]))[^\s\"'<)]{6,})")),
    ("URL key parameter", re.compile(r"[?&]key=(?P<secret>[0-9A-Za-z_-]{16,})")),
]

RISKY_RULES = [
    (re.compile(r"^Bash\(\*?\)$|^Bash$"), "every shell command runs unasked"),
    (re.compile(r"^Bash\((curl|wget)[ :]\*?\*\)"), "any network request outside the sandbox runs unasked"),
    (re.compile(r"unset [^)]*PROXY", re.I), "clearing proxy variables bypasses the sandbox network filter"),
    (re.compile(r"NODE_TLS_REJECT_UNAUTHORIZED"), "turns off TLS certificate checks"),
    (re.compile(r"^Bash\(osascript"), "lets the agent script other apps"),
    (re.compile(r"^Bash\(claude mcp"), "lets the agent add MCP servers to itself"),
    (re.compile(r"^Bash\((sudo|rm -rf)"), "privileged or destructive command"),
    (re.compile(r"^Bash\(git push[ :]\*"), "pushes run unasked"),
    (re.compile(r"^Bash\((python3?|node|npx|bunx?|deno|ruby|perl|bash|sh|zsh|uvx|uv run)(:| )?\*\)$"),
     "runs any code unasked"),
]

# Names of MCP header, env and top-level keys whose value is a credential: `auth` and `key` as words, not
# `author` or `KEYBOARD_LAYOUT`.
CRED_KEY = re.compile(r"(?<![a-z])key(?![a-z])|apikey|token|secret|passw|credential|cookie|bearer|authorization"
                      r"|(?<![a-z])auth(?![a-z])", re.I)

# MCP servers that come with Claude Code or a plugin; rules for them are never "not configured".
BUILTIN_MCP = {"claude-in-chrome", "ide"}

SKIP_DIRS = {".git", "build", ".dart_tool", "node_modules", "Pods", ".fvm", "venv", ".venv", ".gradle", "DerivedData"}
DESCRIPTION_LIMIT = 1536


class Linter:
    def __init__(self, repo: Path, home: Path, config: Path | None = None):
        self.repo = repo
        self.home = home
        self.findings: list[dict] = []
        self.inventory: dict = {}
        self.config_errors: list[tuple[Path, str]] = []  # files that exist but do not parse
        load = self.load
        self.config_path = config or home / CONFIG_FILE
        user_config = load(self.config_path)
        self.config = {**DEFAULTS, **{k: v for k, v in user_config.items() if k in DEFAULTS}}
        self.destructive = {**DESTRUCTIVE_TOOLS, **obj(self.config.get("destructive_tools"))}
        self.user_settings = load(home / ".claude/settings.json")
        self.project_settings = load(repo / ".claude/settings.json")
        self.local_settings = load(repo / ".claude/settings.local.json")
        self.claude_json = load(home / ".claude.json")
        self.mcp_json = load(repo / ".mcp.json")
        self.remote = git_remote_slug(repo)
        # One entry per Claude account: ~/.claude (with ~/.claude.json) and each ~/.claude-* used through
        # CLAUDE_CONFIG_DIR, which keeps its own settings.json, .claude.json and projects/ inside the dir.
        self.accounts = [{"settings_label": "~/.claude/settings.json", "settings": self.user_settings,
                          "json_label": "~/.claude.json", "claude_json": self.claude_json,
                          "projects": home / ".claude/projects", "memory_label": "memory/"}]
        for config in sorted(p for p in home.glob(".claude-*") if p.is_dir()
                             and ((p / "settings.json").exists() or (p / ".claude.json").exists())):
            label = "~/" + config.name
            self.accounts.append({"settings_label": f"{label}/settings.json", "settings": load(config / "settings.json"),
                                  "json_label": f"{label}/.claude.json", "claude_json": load(config / ".claude.json"),
                                  "projects": config / "projects", "memory_label": f"{label}/memory/"})
        self.codex_configs: list[tuple[str, dict]] = []
        self.codex_unread: list[Path] = []
        for path in (home / ".codex/config.toml", repo / ".codex/config.toml"):
            if not path.is_file():
                continue
            if tomllib is None:
                self.codex_unread.append(path)
                continue
            try:
                self.codex_configs.append((self.rel(path), tomllib.loads(path.read_text(encoding="utf-8"))))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                self.codex_unread.append(path)
        self.antigravity_mcp = load(home / ".gemini/config/mcp_config.json")
        self.plugins = self.enabled_plugins()

    def load(self, path: Path) -> dict:
        return load_json(path, self.config_errors)

    def enabled_plugins(self) -> list[tuple[str, Path]]:
        """(name, install folder) of each plugin enabled for this repo, from installed_plugins.json."""
        enabled: dict = {}
        for _, data in self.settings_layers():
            enabled.update(obj(data.get("enabledPlugins")))
        installed = obj(load_json(self.home / ".claude/plugins/installed_plugins.json").get("plugins"))
        out = []
        for name in sorted(n for n, on in enabled.items() if on is True):
            entries = [e for e in (installed.get(name) or []) if isinstance(e, dict) and e.get("installPath")]
            # Prefer this repo's install, then a user-scope one.
            entries.sort(key=lambda e: (e.get("projectPath") != str(self.repo), bool(e.get("projectPath"))))
            if entries and Path(entries[0]["installPath"]).is_dir():
                out.append((name, Path(entries[0]["installPath"])))
        return out

    # -- helpers -------------------------------------------------------------

    def add(self, code: str, severity: str, where: str, message: str, fix: str = "", key: str = ""):
        """`key` tells apart findings that share code and where; it never holds the message, so a
        reworded message keeps the fingerprint and the `id` the user accepts it by."""
        fingerprint = f"{code}|{where}|{key}"
        self.findings.append({
            "id": finding_id(fingerprint),
            "code": code,
            "severity": severity,
            "where": where,
            "message": message,
            "fix": fix,
            "fingerprint": fingerprint,
        })

    def rel(self, path: Path) -> str:
        # Forward slashes on every platform, so labels, ids and the checks comparing them match everywhere.
        for base, label in ((self.repo, ""), (self.home, "~/")):
            try:
                return label + path.relative_to(base).as_posix()
            except ValueError:
                continue
        return path.as_posix()

    def settings_layers(self):
        return [
            ("~/.claude/settings.json", self.user_settings),
            (".claude/settings.json", self.project_settings),
            (".claude/settings.local.json", self.local_settings),
        ]

    def uses(self, agent: str) -> bool:
        return agent in strs(self.config.get("agents"))

    def extra_layers(self):
        """Settings of the extra accounts; a session runs under one of them instead of ~/.claude."""
        return [(a["settings_label"], a["settings"]) for a in self.accounts[1:]]

    def merged_list(self, *keys: str) -> list:
        out = []
        for _, data in self.settings_layers():
            node = data
            for k in keys:
                node = node.get(k, {}) if isinstance(node, dict) else {}
            out.extend(strs(node))
        return out

    def connectors_disabled(self) -> bool:
        # Documented exception to settings precedence: a `true` from any scope wins (Claude Code mcp.md).
        return any(data.get("disableClaudeAiConnectors") is True for _, data in self.settings_layers())

    def setting(self, key: str):
        """Highest-precedence value of a scalar setting (local > project > user)."""
        for _, data in reversed(self.settings_layers()):
            if key in data:
                return data[key]
        return None

    def repo_files(self, *names: str) -> list[Path]:
        try:
            out = subprocess.run(
                # -z: without it git quotes non-ASCII paths (`"ders-\303\247..."`) and they never match.
                ["git", "-C", str(self.repo), "ls-files", "-z", "-co", "--exclude-standard"],
                capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True,
            ).stdout.split("\0")
        except (subprocess.CalledProcessError, FileNotFoundError):
            out = [str(Path(d, n).relative_to(self.repo)) for d, n in walk_files(self.repo)]
        return sorted(self.repo / f for f in out if Path(f).name in names)

    # -- checks --------------------------------------------------------------

    def check_instruction_links(self):
        files = self.repo_files("CLAUDE.md", "AGENTS.md", "GEMINI.md")
        strict = self.config.get("instructions") == "agents-md"
        for f in files:
            sibling_agents = f.parent / "AGENTS.md"
            if f.name == "CLAUDE.md":
                # One source of truth: CLAUDE.md points at AGENTS.md by a symlink or by an `@AGENTS.md` import
                # (the import also works where symlinks do not, such as Windows checkouts).
                if not f.is_symlink():
                    if not imports_agents_md(read_text(f)) and (strict or sibling_agents.exists()):
                        # by default only two real files can drift
                        self.add("link-claude-md", "error" if strict else "warn", self.rel(f),
                                 "CLAUDE.md has its own content instead of pointing at AGENTS.md"
                                 + ("" if strict else "; the two files can drift apart"),
                                 "Move its content into AGENTS.md, then make CLAUDE.md a symlink "
                                 "(`ln -sf AGENTS.md CLAUDE.md`) or a one-line `@AGENTS.md` import", key="real-file")
                elif f.resolve() != sibling_agents.resolve() or not sibling_agents.exists():
                    self.add("link-claude-md", "error", self.rel(f),
                             f"CLAUDE.md points to {os.readlink(f)}, not the AGENTS.md beside it",
                             "`ln -sf AGENTS.md CLAUDE.md`", key="wrong-target")
            elif f.name == "AGENTS.md" and self.uses("claude") and not (f.parent / "CLAUDE.md").exists():
                self.add("link-claude-md", "warn", self.rel(f),
                         "AGENTS.md has no CLAUDE.md symlink beside it, so Claude Code does not read it",
                         "`ln -s AGENTS.md CLAUDE.md` in that folder", key="no-link")
            elif f.name == "GEMINI.md" and not self.uses("gemini-cli"):
                self.add("gemini-cli", "warn", self.rel(f),
                         "GEMINI.md beside AGENTS.md: a second instruction file that can drift from it",
                         "Merge anything unique into AGENTS.md, then `git rm` it")

    def check_repo_skills_link(self):
        for agents_skills in self.skill_roots():
            claude_skills = agents_skills.parent.parent / ".claude/skills"
            label = self.rel(claude_skills)
            if claude_skills.is_symlink():
                if claude_skills.resolve() != agents_skills.resolve():
                    self.add("link-repo-skills", "error", label,
                             f"{label} points to {os.readlink(claude_skills)}, not ../.agents/skills",
                             f"`ln -sfn ../.agents/skills {label}`", key="wrong-target")
                continue
            if claude_skills.is_dir():
                shared = {p.name for p in skill_dirs(agents_skills)}
                missing = sorted(shared - {p.name for p in claude_skills.iterdir() if p.exists()})  # dangling = missing
                # Skills that exist only here would die with the folder: move them first.
                only_here = sorted(p.name for p in skill_dirs(claude_skills) if p.name not in shared)
                # Real copies that drifted, and anything that is not a skill, would be lost with the folder.
                differs, not_skill = [], []
                for p in sorted(claude_skills.iterdir()):
                    if p.is_symlink():
                        continue
                    if not (p.is_dir() and (p / "SKILL.md").exists()):
                        not_skill.append(p.name)
                    elif p.name in shared and not same_tree(p, agents_skills / p.name):
                        differs.append(p.name)
                tracked = subprocess.run(["git", "-C", str(self.repo), "ls-files", "--", label],
                                         capture_output=True, text=True).stdout.strip()
                steps = [f"mv {label}/{n} {self.rel(agents_skills)}/" for n in only_here]
                steps += [f"{'git rm -r' if tracked else 'rm -r'} {label}", f"ln -s ../.agents/skills {label}"]
                if differs or not_skill:
                    fix = ("Resolve these first, then re-run the audit: "
                           + "; ".join([f"`diff -r {label}/{n} {self.rel(agents_skills)}/{n}` and merge" for n in differs]
                                       + [f"move or delete `{label}/{n}`" for n in not_skill]))
                else:
                    fix = "`" + " && ".join(steps) + "`"
                self.add("link-repo-skills", "warn", label,
                         "per-skill links instead of one directory symlink"
                         + (f"; missing for Claude: {', '.join(missing)}" if missing else "")
                         + (f"; only in .claude/skills: {', '.join(only_here)}" if only_here else "")
                         + (f"; differs from .agents/skills: {', '.join(differs)}" if differs else "")
                         + (f"; not a skill: {', '.join(not_skill)}" if not_skill else ""),
                         fix, key="per-skill")
            else:
                self.add("link-repo-skills", "error", label,
                         f"{label} is missing, so Claude sees none of the skills in {self.rel(agents_skills)}",
                         f"`ln -s ../.agents/skills {label}`", key="missing")

    def check_skill_placement(self):
        """A SKILL.md outside any `.agents/skills` or `.claude/skills` folder is loaded by no agent."""
        by_folder: dict[Path, list[str]] = {}
        for f in self.skill_files():
            folder = f.parent.parent
            if folder.name == "skills" and folder.parent.name == ".claude":
                continue
            if folder not in self.skill_roots():
                by_folder.setdefault(f.parent.parent, []).append(f.parent.name)
        for folder, names in sorted(by_folder.items()):
            label = self.rel(folder)
            self.add("skill-misplaced", "warn", label,
                     f"{len(names)} skill(s) no agent loads: {', '.join(sorted(names))}",
                     f"Delete the dead ones; move the rest to `{self.rel(folder.parent)}/.agents/skills` "
                     "and link `.claude/skills` to it", key=label)

    def check_skill_refs(self):
        """Paths a repo skill names that no longer exist: running it sends the agent to dead files."""
        for root in self.skill_roots():
            base = root.parent.parent
            for d in skill_dirs(root):
                text = read_text(d / "SKILL.md")
                missing = []
                for ref in skill_path_refs(text):
                    soft, ref = ref.startswith("?"), ref.lstrip("?")
                    if self.ref_exists(ref, d, base) or (soft and self.ref_exists(str(Path(ref).parent), d, base)):
                        continue
                    if ref not in missing:
                        missing.append(ref)
                if missing:
                    shown = ", ".join(f"`{m}`" for m in missing[:6]) + (" …" if len(missing) > 6 else "")
                    self.add("skill-dead-ref", "warn", self.rel(d / "SKILL.md"),
                             f"names {len(missing)} path(s) that do not exist: {shown}",
                             "Fix the paths, or delete the skill if its inputs are gone", key=d.name)

    def ref_exists(self, ref: str, skill_dir: Path, base: Path) -> bool:
        if ref.startswith("~"):
            return Path(ref.replace("~", str(self.home), 1)).exists()
        if any((c / ref).exists() for c in (skill_dir, base, self.repo)):
            return True
        # Skills often name paths relative to a subfolder of their feature (`n4/seed/x.json`).
        return not ref.startswith(("/", "..")) and exists_below(base, ref.lstrip("./").rstrip("/"))

    def check_global_skill_links(self):
        source = self.home / ".agents/skills"
        if not source.is_dir():
            return
        # Every Claude config dir (~/.claude, and ~/.claude-* used through CLAUDE_CONFIG_DIR) needs its own links.
        config_dirs = [self.home / ".claude"] + sorted(p for p in self.home.glob(".claude-*") if (p / "skills").is_dir())
        seen: set[Path] = set()
        for config in config_dirs:
            real = (config / "skills").resolve()
            if real in seen:  # ~/.claude-*/skills linked to ~/.claude/skills: same folder, warn once
                continue
            seen.add(real)
            target = config / "skills"
            label = "~/" + config.name + "/skills"
            for p in skill_dirs(source):
                if not (target / p.name).exists():
                    self.add("link-global-skills", "warn", f"{label}/{p.name}",
                             f"global skill has no link in {label}, so Claude does not see it",
                             f"`ln -s ../../.agents/skills/{p.name} {label}/{p.name}`", key="no-link")
            if target.is_dir():
                for p in sorted(target.iterdir()):
                    if p.is_symlink() and not p.exists():
                        self.add("link-global-skills", "warn", f"{label}/{p.name}",
                                 f"broken link to {os.readlink(p)}", f"`rm {label}/{p.name}`", key="broken")

    def check_config_dirs(self):
        """Extra Claude config dirs (CLAUDE_CONFIG_DIR, one per account) drift from ~/.claude."""
        main = self.home / ".claude"
        main_claude_md = read_text(main / "CLAUDE.md")
        for config in sorted(p for p in self.home.glob(".claude-*") if (p / "settings.json").exists()):
            label = "~/" + config.name
            settings = load_json(config / "settings.json")
            if self.user_settings.get("disableClaudeAiConnectors") and not settings.get("disableClaudeAiConnectors"):
                self.add("config-drift", "warn", f"{label}/settings.json",
                         "claude.ai connectors are off in ~/.claude but on for this account",
                         'Add `"disableClaudeAiConnectors": true`')
            claude_md = config / "CLAUDE.md"
            if claude_md.is_symlink() and not claude_md.exists():
                self.add("config-drift", "warn", f"{label}/CLAUDE.md", f"broken link to {os.readlink(claude_md)}",
                         f"`ln -sf ../.claude/CLAUDE.md {label}/CLAUDE.md`", key="broken")
            elif not claude_md.exists() and main_claude_md:
                self.add("config-drift", "warn", f"{label}/CLAUDE.md",
                         "no CLAUDE.md, so this account misses the global instructions in ~/.claude/CLAUDE.md",
                         f"`ln -s ../.claude/CLAUDE.md {label}/CLAUDE.md`", key="missing")
            elif claude_md.exists() and not claude_md.is_symlink() and read_text(claude_md) != main_claude_md:
                self.add("config-drift", "warn", f"{label}/CLAUDE.md",
                         "global instructions differ from ~/.claude/CLAUDE.md",
                         f"Merge them, then `ln -sf ../.claude/CLAUDE.md {label}/CLAUDE.md`")
            skills = config / "skills"
            if skills.is_symlink() and not skills.exists():
                self.add("config-drift", "warn", f"{label}/skills", f"broken link to {os.readlink(skills)}",
                         f"`ln -sfn ../.claude/skills {label}/skills`", key="broken")
            elif not skills.exists() and not skills.is_symlink():
                self.add("config-drift", "warn", f"{label}/skills",
                         "no skills folder, so this account sees none of the global skills",
                         f"`ln -s ../.claude/skills {label}/skills`", key="missing")
            elif skills.exists() and not (skills.is_symlink() and skills.resolve() == (main / "skills").resolve()):
                if skills.is_symlink():
                    fix = f"`ln -sfn ../.claude/skills {label}/skills`"
                else:
                    # `ln -sfn` onto a real directory would put the link inside it (macOS and GNU alike).
                    fix = (f"Move its own skills to ~/.agents/skills, remove the links left in it, then "
                           f"`rmdir {label}/skills && ln -s ../.claude/skills {label}/skills`")
                self.add("config-drift", "warn", f"{label}/skills", "skills folder is not a link to ~/.claude/skills", fix)

    def check_skill_duplicates(self):
        repo_skills = {p.name: p for p in skill_dirs(self.repo / ".agents/skills")}
        global_skills = {p.name: p for p in skill_dirs(self.home / ".agents/skills")}
        for name in sorted(repo_skills.keys() & global_skills.keys()):
            same = read_text(repo_skills[name] / "SKILL.md") == read_text(global_skills[name] / "SKILL.md")
            self.add("skill-duplicate", "warn", f".agents/skills/{name}",
                     "same skill exists globally" + (" (identical)" if same else " and the copies differ")
                     + "; Claude uses the global one, Codex lists both",
                     "Keep one source: update the global copy, then `git rm -r` the repo copy",
                     key=name)

    def check_skill_frontmatter(self):
        roots = self.skill_roots() + [self.home / ".agents/skills"]
        claude_global = self.home / ".claude/skills"
        if claude_global.is_dir():
            roots.append(claude_global)
        seen: set[Path] = set()
        for root in roots:
            for d in skill_dirs(root):
                real = d.resolve()
                if real in seen:
                    continue
                seen.add(real)
                skill_md = d / "SKILL.md"
                meta = frontmatter(read_text(skill_md))
                where = self.rel(skill_md)
                if meta.get("name") and meta["name"] != d.name:
                    self.add("skill-frontmatter", "warn", where,
                             f"name `{meta['name']}` differs from folder `{d.name}`; overrides and links use the folder", key="name")
                desc = meta.get("description", "")
                if not desc:
                    self.add("skill-frontmatter", "error", where, "no description in frontmatter", key="no-description")
                elif len(desc) + len(meta.get("when_to_use", "")) > DESCRIPTION_LIMIT:
                    self.add("skill-frontmatter", "warn", where,
                             f"description is {len(desc) + len(meta.get('when_to_use', ''))} chars; "
                             f"Claude truncates the listing at {DESCRIPTION_LIMIT}",
                             key="long-description")
                for target in markdown_links(read_text(skill_md)):
                    if "." not in Path(target).name:  # placeholders such as `(link)`
                        continue
                    path = Path(str(self.home) + target[1:]) if target.startswith("~") else d / target
                    if not path.exists():
                        # A bare file name is often an example inside prose; a path is a real pointer.
                        severity = "warn" if "/" in target else "info"
                        self.add("skill-frontmatter", severity, where, f"links to missing file `{target}`", key=target)

    def check_context_budget(self):
        overrides = {}
        for _, data in self.settings_layers():
            overrides.update(obj(data.get("skillOverrides")))
        visible: dict[str, Path] = {}
        for root in (self.repo / ".claude/skills", self.home / ".claude/skills"):  # personal wins, so it goes last
            for d in skill_dirs(root):
                visible[d.name] = d
        for plugin, path in self.plugins:  # plugin skills are listed as `<plugin>:<skill>`
            for d in skill_dirs(path / "skills"):
                visible.setdefault(f"{plugin.split('@')[0]}:{d.name}", d)
        synced = self.home / ".claude/skills/synced"
        if self.setting("syncClaudeAiSkills") is not False and synced.is_dir():
            for bucket in synced.iterdir():
                if bucket.is_dir():
                    for d in skill_dirs(bucket):
                        visible.setdefault(f"claude.ai:{d.name}", d)
        rows = []  # hidden skills stay in the list with their state, so the skill review sees them too
        for name, d in visible.items():
            meta = frontmatter(read_text(d / "SKILL.md"))
            override = overrides.get(name, "on")
            if override == "off":
                state = "off"
            elif override == "user-invocable-only" or str(meta.get("disable-model-invocation")).lower() == "true":
                state = "user-only"
            else:
                state = "name-only" if override == "name-only" else "listed"
            chars = len(name) + (0 if state == "name-only" else len(meta.get("description", "")))
            rows.append((state not in ("listed", "name-only"), -chars, name, state, chars))
        rows.sort()
        listed = [r for r in rows if not r[0]]
        instr = [self.repo / "CLAUDE.md", self.home / ".claude/CLAUDE.md", self.memory_dir() / "MEMORY.md"]
        instr_chars = {self.rel(p): len(read_text(p)) for p in instr if p.exists()}
        servers = self.mcp_servers()
        self.inventory = {
            "remote": self.remote,
            "skills_listed": len(listed),
            "skill_listing_tokens_est": sum(r[4] for r in listed) // 4,
            "skills": [{"name": r[2], "chars": r[4], "state": r[3]} for r in rows],
            "instruction_files_chars": instr_chars,
            "mcp_servers": servers,
            "claude_ai_connectors": "off" if self.connectors_disabled() else "on",
            "claude_ai_skill_sync": "off" if self.setting("syncClaudeAiSkills") is False else "on",
            "config": {"file": self.rel(self.config_path) if self.config_path.exists() else None,
                       **{k: v for k, v in self.config.items() if k != "destructive_tools"}},
        }

    def check_mcp(self):
        if self.config.get("claude_ai_connectors") == "off" and not self.connectors_disabled():
            self.add("mcp-connectors", "warn", ".claude/settings.local.json",
                     "claude.ai connectors are loaded; every connected app adds tools to each session",
                     'Add `"disableClaudeAiConnectors": true`, or turn single ones off in `/mcp`')
        for agent, name, spec in self.every_mcp_spec():
            where = spec["_source"]
            args = [str(a) for a in spec.get("args", []) or []]
            # `npx pkg@latest` is fine by policy: the user wants updates without tracking versions.
            for a in [spec.get("command", "")] + args:
                if re.search(r"/versions?/\d+\.\d+", str(a)):
                    self.add("mcp-unpinned", "warn", f"{where}:{name}",
                             f"hardcoded SDK version path `{a}` breaks when the SDK updates",
                             "Start it through the version manager's entry point instead of a versioned path", key=a)
            if agent == "claude" and self.mcp_enabled(name, spec) and name in self.destructive:
                deny = self.deny_rules(spec)
                whole = {f"mcp__{name}", f"mcp__{name}__*"} & deny  # a server-level deny covers every tool
                missing = [] if whole else [t for t in strs(self.destructive[name]) if f"mcp__{name}__{t}" not in deny]
                if missing:
                    self.add("mcp-destructive", "error", f"{where}:{name}",
                             f"destructive tools without a deny rule: {', '.join(missing)}",
                             "Add `mcp__" + name + "__<tool>` entries to `permissions.deny` in .claude/settings.local.json")

    def check_permissions(self):
        configured = {name for name, _ in self.all_mcp_specs()}
        perms = obj(self.project_settings.get("permissions"))
        if self.config.get("permission_rules") == "local" and any(strs(perms.get(k)) for k in ("allow", "ask", "deny")):
            # Claude reads the shared file only from the start dir, but the local one from the repo root.
            subfolders = sorted({self.rel(f.parent) for f in self.repo_files("AGENTS.md") if f.parent != self.repo}
                                | {self.rel(r.parent.parent) for r in self.skill_roots() if r.parent.parent != self.repo})
            if subfolders:
                shown = ", ".join(subfolders[:4]) + (" …" if len(subfolders) > 4 else "")
                self.add("perm-scope", "warn", ".claude/settings.json",
                         f"its permission rules reach only agents started at the repo root; agents started in {shown} miss them",
                         "Move the rules to .claude/settings.local.json, which Claude reads from the repo root")
        for label, data in self.settings_layers() + self.extra_layers():
            perms = obj(data.get("permissions"))
            for kind in ("allow", "ask", "deny"):
                for rule in strs(perms.get(kind)):
                    self.check_rule(label, kind, rule, configured)

    def check_mcp_secrets(self):
        fixes = {
            "claude": "Use `${VAR}` expansion and export the value from your shell profile",
            "codex": "Use `env_http_headers` (header -> env var name) or `bearer_token_env_var`",
            "antigravity": "Keep it only here; Antigravity has no env-var form for it",
        }
        for agent, name, spec in self.every_mcp_spec():
            kinds = inline_secrets(spec)
            if kinds:
                where = spec["_source"]
                # A file inside the repo can be committed; one under ~ stays on this machine.
                self.add("perm-secret", "warn" if where.startswith("~") else "error", f"{where}:{name}",
                         f"secret written inline in the MCP config ({', '.join(kinds)})", fixes[agent],
                         key="inline-secret")

    def check_rule(self, label: str, kind: str, rule: str, configured: set[str]):
        clean, secret_kinds = redact(rule)
        if secret_kinds:
            self.add("perm-secret", "error", label,
                     f"{', '.join(secret_kinds)} inside a permission rule: `{clean}`",
                     "Delete the rule; keep the secret in .env",
                     key=clean)
            return
        if kind == "allow":
            whys = [why for rx, why in RISKY_RULES if rx.search(rule)]
            if whys:  # one finding per rule, so its id stays unique
                self.add("perm-risky", "warn", label, f"`{rule}`: {'; '.join(whys)}", "Remove it from `allow`", key=rule)
        m = re.match(r"mcp__([^_]+(?:_[^_]+)*?)__", rule)
        server = m.group(1) if m else ""
        if server and server not in configured and server not in BUILTIN_MCP and not server.startswith(("claude_ai", "plugin_")):
            self.add("perm-stale", "info", label, f"`{rule}`: MCP server `{server}` is not configured",
                     "Remove the rule" + ("" if kind == "allow" else "; add it back if the server returns"),
                     key="mcp:" + rule)
        for path in re.findall(r"((?:/Users|/home)/[^\s\"')*]+|~/[^\s\"')*]+)", rule):
            p = Path(path.replace("~", str(self.home), 1)).expanduser()
            probe = p if "*" not in str(p) else Path(str(p).split("*")[0])
            if not probe.exists() and not probe.parent.exists():
                self.add("perm-stale", "info", label, f"`{rule[:100]}`: path `{path}` no longer exists",
                         "Remove the rule", key="path:" + rule)
                break
        else:
            if kind == "allow" and len(rule) > 120:
                self.add("perm-stale", "info", label, f"one-off rule ({len(rule)} chars): `{rule[:80]}…`",
                         "Remove it; sandboxed commands run without it", key="long:" + rule)

    def check_global_leak(self):
        if not self.remote:
            return
        owner = self.remote.split("/")[0]
        for label, data in [self.settings_layers()[0]] + self.extra_layers():
            # `/Users/<owner>/…` is a home path, not a repo slug.
            slugs = set(re.findall(rf"(?<!/Users/)(?<!/home/)\b{re.escape(owner)}/[A-Za-z0-9_.-]+", json.dumps(data)))
            for slug in sorted(slugs - {self.remote}):
                self.add("global-leak", "info", label,
                         f"names repo `{slug}`, not this repo's remote `{self.remote}`; global settings apply in every repo",
                         "Fix the name if that repo was renamed; otherwise accept it", key=slug)
            if self.remote in slugs:
                self.add("global-leak", "info", label,
                         f"global settings hold content specific to `{self.remote}`; it applies in every repo",
                         "`autoMode` only works in user settings, so keep it generic or accept it", key=self.remote)

    def check_doc_refs(self):
        files = self.repo_files("AGENTS.md")
        for f in files + [self.home / ".claude/CLAUDE.md"]:
            text = read_text(f)
            for ref in set(re.findall(r"`([^`\s]+)`", text)) | set(markdown_links(text)):
                if not looks_like_path(ref):
                    continue
                candidates = [f.parent / ref, self.repo / ref, Path(ref.replace("~", str(self.home), 1))]
                if any(c.exists() for c in candidates):
                    continue
                # Docs often name paths relative to a subfolder (`src/x.dart` meaning `lib/src/x.dart`).
                in_repo = str(f).startswith(str(self.repo))  # never walk all of ~/.claude for a global file
                if in_repo and not ref.startswith(("~", "/", "..")) and exists_below(f.parent, ref.lstrip("./")):
                    continue
                else:
                    self.add("doc-refs", "warn", self.rel(f), f"refers to `{ref}`, which does not exist",
                             "Update or delete the line", key=ref)

    def check_memory(self):
        for account in self.accounts:
            mem = self.memory_dir(account["projects"])
            index = mem / "MEMORY.md"
            if not index.exists():
                continue
            label = account["memory_label"]
            text = read_text(index)
            if not text.strip():
                self.add("memory", "info", f"{label}MEMORY.md", "empty memory index", "Delete it, or add pointers",
                         key="empty")
            elif len(text.splitlines()) > 180:
                self.add("memory", "warn", f"{label}MEMORY.md",
                         f"{len(text.splitlines())} lines; Claude loads only the first 200 lines of MEMORY.md",
                         "Keep one line per memory; move detail into the topic files", key="long")
            linked = set(re.findall(r"\]\(([^)]+\.md)\)", text))
            for name in sorted(linked):
                if not (mem / name).exists():
                    self.add("memory", "warn", f"{label}{name}", "listed in MEMORY.md but the file is missing",
                             "Remove the line from MEMORY.md")
            for p in sorted(mem.glob("*.md")):
                if p.name != "MEMORY.md" and p.name not in linked:
                    self.add("memory", "warn", f"{label}{p.name}", "memory file is not listed in MEMORY.md",
                             "Add a one-line pointer or delete the file")

    def check_other_agents(self):
        if not self.uses("gemini-cli"):
            # Only the repo's .gemini/ (Gemini CLI settings). ~/.gemini holds Antigravity's live config
            # (config/, skills/, hooks), so nothing under it is ever a leftover to delete.
            for p in (self.repo / ".gemini",):
                if p.exists():
                    self.add("gemini-cli", "warn", self.rel(p), "Gemini CLI leftover", f"Delete `{self.rel(p)}`")
        for sub in ("rules", "workflows"):
            p = self.repo / ".agents" / sub
            if p.is_dir() and any(p.iterdir()):
                self.add("antigravity-only", "info", self.rel(p),
                         f"read by Antigravity only; Claude and Codex never see .agents/{sub}",
                         "Move rules every agent needs into AGENTS.md")

    def check_hygiene(self):
        tracked = subprocess.run(["git", "-C", str(self.repo), "ls-files", ".claude/settings.local.json"],
                                 capture_output=True, text=True).stdout.strip()
        if tracked:
            self.add("hygiene", "error", ".claude/settings.local.json", "personal settings file is tracked by git",
                     "`git rm --cached .claude/settings.local.json`", key="tracked")
        shared = subprocess.run(["git", "-C", str(self.repo), "ls-files", ".claude/settings.json"],
                                capture_output=True, text=True).stdout.strip()
        if shared and self.config.get("permission_rules") == "local":
            self.add("hygiene", "warn", ".claude/settings.json",
                     "shared settings file is committed; Claude reads it only in sessions started at the repo root",
                     "Move its settings to .claude/settings.local.json, then `git rm --cached .claude/settings.json`",
                     key="committed")
        out = subprocess.run(["git", "-C", str(self.repo), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True).stdout
        for block in out.split("\n\n"):
            if "prunable" in block:
                path = block.splitlines()[0].removeprefix("worktree ")
                self.add("hygiene", "warn", path, "worktree folder is gone but still registered",
                         "`git worktree prune`", key=path)
        enabled = None
        for _, data in self.settings_layers():  # `sandbox` objects merge across files; the last `enabled` wins
            sandbox = obj(data.get("sandbox"))
            if "enabled" in sandbox:
                enabled = sandbox["enabled"]
        if not enabled:
            self.add("hygiene", "info", ".claude/settings.local.json", "sandbox is not enabled for this repo", key="sandbox")

    def check_risky_settings(self):
        for label, data in self.settings_layers() + self.extra_layers():
            for k, v in obj(data.get("env")).items():
                if (CRED_KEY.search(str(k)) and literal_value(v)) or (isinstance(v, str) and redact(v)[1]):
                    self.add("perm-secret", "error" if label == ".claude/settings.json" else "warn", label,
                             f"env `{k}` holds a secret value", "Export it from your shell profile instead", key=f"env:{k}")
            if obj(data.get("permissions")).get("defaultMode") == "bypassPermissions":
                self.add("perm-mode", "warn", label,
                         "`defaultMode` is `bypassPermissions`: every tool call runs without asking, deny rules aside",
                         'Remove it, or use `"acceptEdits"`; turn bypass on per session when you need it', key="bypass")
            if label == ".claude/settings.json" and data.get("enableAllProjectMcpServers") is True:
                self.add("mcp-autoenable", "warn", label,
                         "`enableAllProjectMcpServers` is on: every server in .mcp.json starts unasked for anyone who has this file",
                         "List the servers you use in `enabledMcpjsonServers` in .claude/settings.local.json")

    def check_codex(self):
        for path in self.codex_unread:
            why = (f"Python {sys.version_info[0]}.{sys.version_info[1]} has no tomllib" if tomllib is None
                   else "it is not valid TOML")
            self.add("codex-config", "info", self.rel(path), f"Codex config not checked: {why}",
                     "Run lint.py with Python 3.11+" if tomllib is None else "Fix the TOML syntax")
        trust = None
        for label, data in self.codex_configs:
            whys = []
            if data.get("sandbox_mode") == "danger-full-access":
                whys.append('`sandbox_mode = "danger-full-access"`: commands run with no sandbox')
            if data.get("approval_policy") == "never":
                whys.append('`approval_policy = "never"`: Codex never asks before a command')
            if whys:
                self.add("codex-risky", "warn", label, "; ".join(whys),
                         'Use `sandbox_mode = "workspace-write"` and `approval_policy = "on-request"`')
            project = (data.get("projects") or {}).get(str(self.repo)) or {}
            trust = project.get("trust_level", trust)
        if self.codex_configs:
            self.inventory["codex"] = {"configs": [label for label, _ in self.codex_configs], "trust_level": trust}

    def hook_sources(self):
        for label, data in self.settings_layers() + self.extra_layers():
            yield "claude", label, obj(data.get("hooks"))
        for path in (self.home / ".codex/hooks.json", self.repo / ".codex/hooks.json"):
            yield "codex", self.rel(path), self.load(path)
        yield "antigravity", "~/.gemini/config/hooks.json", self.load(self.home / ".gemini/config/hooks.json")

    def check_hooks(self):
        """Every hook command runs on its own, outside the agent's permission checks: list them for review."""
        hooks, flagged = [], set()
        for agent, label, data in self.hook_sources():
            for event, command in walk_hooks(data):
                shown, kinds = redact(command)
                if kinds and (label, event) not in flagged:
                    flagged.add((label, event))
                    self.add("perm-secret", "warn", label, f"{event} hook command holds a {', '.join(kinds)}",
                             "Read it from an env var or a file the script loads", key=f"hook:{event}")
                hooks.append({"agent": agent, "source": label, "event": event,
                              "command": shown if len(shown) <= 200 else shown[:200] + "…"})
                script = self.hook_script(command)
                if script and not script.exists():
                    where = self.rel(script)
                    self.add("hook-missing", "warn", label, f"{event} hook runs `{where}`, which does not exist",
                             "Restore the script or remove the hook", key=f"{event}:{where}")
        self.inventory["hooks"] = hooks

    def hook_script(self, command: str) -> Path | None:
        """The script file a hook command runs, when it names one by an absolute, ~ or variable path."""
        for var, value in (("CLAUDE_PROJECT_DIR", self.repo), ("HOME", self.home)):
            command = command.replace("${" + var + "}", str(value)).replace("$" + var, str(value))
        try:
            tokens = shlex.split(command)
        except ValueError:
            return None
        for t in tokens:
            if t.startswith("~/"):
                t = str(self.home) + t[1:]
            if t.startswith("/") and re.search(r"\.(sh|bash|zsh|py|js|mjs|cjs|ts|rb|pl)$", t):
                return Path(t)
        return None

    def check_agent_files(self):
        """What else Claude loads: rules, CLAUDE.local.md, subagents, commands, plugins, @imports."""
        inv = self.inventory
        chars = inv.setdefault("instruction_files_chars", {})
        rules = []
        for base in (self.repo / ".claude/rules", self.home / ".claude/rules"):
            for d, n in (walk_files(base) if base.is_dir() else []):
                if n.endswith(".md"):
                    rules.append(Path(d, n))
        rules.sort(key=lambda p: (not str(p).startswith(str(self.repo)), str(p)))
        inv["claude_rules"] = [self.rel(p) for p in rules]
        for p in rules:  # a rule with `paths:` loads only when Claude touches a matching file
            if "paths" not in frontmatter(read_text(p)):
                chars[self.rel(p)] = len(read_text(p))
        local = self.repo / "CLAUDE.local.md"
        if local.exists():
            chars[self.rel(local)] = len(read_text(local))
        for kind in ("agents", "commands"):
            found = []
            for base in (self.repo / ".claude" / kind, self.home / ".claude" / kind):
                if base.is_dir():
                    found += sorted(self.rel(Path(d, n)) for d, n in walk_files(base) if n.endswith(".md"))
            inv[kind] = found
        plugins: dict = {}
        for _, data in self.settings_layers():
            plugins.update(data.get("enabledPlugins") or {})
        inv["plugins"] = sorted(name for name, on in plugins.items() if on is True)
        self.check_imports(rules + [local])

    def check_imports(self, extra: list[Path]):
        """Claude follows `@path` imports in its instruction files; Codex and Antigravity read them as text."""
        seen, imports = set(), []
        for f in self.repo_files("AGENTS.md", "CLAUDE.md") + [self.home / ".claude/CLAUDE.md"] + extra:
            if not f.exists() or f.resolve() in seen:
                continue
            seen.add(f.resolve())
            where = self.rel(f)
            for ref in instruction_imports(read_text(f)):
                imports.append({"file": where, "import": ref})
                target = Path(str(self.home) + ref[1:]) if ref.startswith("~/") else f.parent / ref
                if not target.exists():
                    self.add("doc-import", "warn", where, f"imports `@{ref}`, which does not exist",
                             "Fix the path or remove the import", key=ref)
                elif f.resolve().name == "AGENTS.md":
                    self.add("doc-import", "info", where,
                             f"imports `@{ref}`: Claude loads it, Codex and Antigravity see only the text",
                             "Inline what every agent needs into AGENTS.md, or accept it", key=ref)
        self.inventory["imports"] = imports

    def check_config_invalid(self):
        seen = set()
        for path, why in self.config_errors:
            if path in seen:
                continue
            seen.add(path)
            self.add("config-invalid", "error", self.rel(path), f"{why}; every agent reading it treats it as empty",
                     "Fix the syntax; `python3 -m json.tool <file> >/dev/null` names the spot")

    def check_stale_names(self):
        """Repo settings that name a skill or .mcp.json server that is gone: leftovers of a removal."""
        skills = {s["name"] for s in self.inventory.get("skills", [])}
        for root in self.skill_roots() + [self.home / ".agents/skills", self.home / ".claude/skills"]:
            skills |= {d.name for d in skill_dirs(root)}
        servers = set(obj(self.mcp_json.get("mcpServers")))
        for label, data in self.settings_layers()[1:]:  # user settings serve every repo, so only the repo's files
            for name in obj(data.get("skillOverrides")):
                if name not in skills:
                    self.add("stale-name", "info", label, f"`skillOverrides` names `{name}`, which no longer exists",
                             "Remove the entry", key=f"skillOverrides:{name}")
            for key in ("enabledMcpjsonServers", "disabledMcpjsonServers"):
                for name in strs(data.get(key)):
                    if name not in servers:
                        self.add("stale-name", "info", label, f"`{key}` names `{name}`, which is not in .mcp.json",
                                 "Remove the entry", key=f"{key}:{name}")

    def check_mcp_duplicates(self):
        """One server configured in many places: a key change or a removal has to be repeated in each."""
        places: dict[str, list[str]] = {}
        for _, name, spec in self.every_mcp_spec():
            if spec["_source"] not in places.setdefault(name, []):
                places[name].append(spec["_source"])
        for name, sources in sorted(places.items()):
            if len(sources) > 1:
                self.add("mcp-duplicate", "info", name, f"configured in {len(sources)} places: {', '.join(sources)}",
                         "Keep it only where an agent uses it; change its key in every place at once")

    # -- MCP helpers ---------------------------------------------------------

    def all_mcp_specs(self):
        """Claude's servers: the repo's .mcp.json, then each account's global and per-project ones."""
        for name, spec in obj(self.mcp_json.get("mcpServers")).items():
            if isinstance(spec, dict):
                yield name, {**spec, "_source": ".mcp.json", "_account": 0}
        for plugin, path in self.plugins:
            data = load_json(path / ".mcp.json")
            for name, spec in obj(data.get("mcpServers", data)).items():
                if isinstance(spec, dict):
                    # Claude names a plugin's server `plugin_<plugin>_<server>` in tool names and logs.
                    yield f"plugin_{plugin.split('@')[0]}_{name}", {**spec, "_source": f"plugin {plugin}", "_account": 0}
        for i, account in enumerate(self.accounts):
            state = self.account_project(i)
            for scope, servers in (("", account["claude_json"].get("mcpServers")), (" (project)", state.get("mcpServers"))):
                for name, spec in obj(servers).items():
                    if isinstance(spec, dict):
                        yield name, {**spec, "_source": account["json_label"] + scope, "_account": i}

    def every_mcp_spec(self):
        """(agent, name, spec) for Claude, Codex and Antigravity servers."""
        for name, spec in self.all_mcp_specs():
            yield "claude", name, spec
        for label, data in self.codex_configs:
            for name, spec in obj(data.get("mcp_servers")).items():
                if isinstance(spec, dict):
                    yield "codex", name, {**spec, "_source": label}
        for name, spec in obj(self.antigravity_mcp.get("mcpServers")).items():
            if isinstance(spec, dict):
                yield "antigravity", name, {**spec, "_source": "~/.gemini/config/mcp_config.json"}

    def account_project(self, i: int) -> dict:
        return obj(obj(self.accounts[i]["claude_json"].get("projects")).get(str(self.repo)))

    def deny_rules(self, spec: dict) -> set:
        """Deny rules a session using this server sees: its account's user settings plus the repo's files."""
        i = spec.get("_account", 0)
        layers = [self.accounts[i]["settings"], self.project_settings, self.local_settings]
        return {r for data in layers for r in strs(obj(data.get("permissions")).get("deny"))}

    def mcp_enabled(self, name: str, spec: dict) -> bool:
        if name in (self.account_project(spec.get("_account", 0)).get("disabledMcpServers") or []):
            return False
        if spec["_source"] != ".mcp.json":
            return True
        # Approving a .mcp.json server in Claude's dialog writes it to the project entry of .claude.json.
        approved = lambda key: any(name in strs(self.account_project(i).get(key)) for i in range(len(self.accounts)))
        if name in self.merged_list("disabledMcpjsonServers") or approved("disabledMcpjsonServers"):
            return False
        return (bool(self.setting("enableAllProjectMcpServers")) or name in self.merged_list("enabledMcpjsonServers")
                or approved("enabledMcpjsonServers"))

    def mcp_servers(self) -> list[dict]:
        out = []
        for agent, name, spec in self.every_mcp_spec():
            if agent == "claude":
                enabled = self.mcp_enabled(name, spec)
            elif agent == "codex":
                enabled = spec.get("enabled", True) is not False
            else:
                enabled = spec.get("disabled") is not True
            out.append({"agent": agent, "name": name, "source": spec["_source"], "enabled": enabled})
        return out

    def memory_dir(self, projects: Path | None = None) -> Path:
        return (projects or self.home / ".claude/projects") / re.sub(r"[^A-Za-z0-9]", "-", str(self.repo)) / "memory"

    # -- skill helpers -------------------------------------------------------

    def skill_files(self) -> list[Path]:
        return self.repo_files("SKILL.md")

    def skill_roots(self) -> list[Path]:
        """Every `<dir>/.agents/skills` in the repo: the root one and each feature's own."""
        roots = {f.parent.parent for f in self.skill_files()
                 if f.parent.parent.name == "skills" and f.parent.parent.parent.name == ".agents"}
        if (self.repo / ".agents/skills").is_dir():
            roots.add(self.repo / ".agents/skills")
        return sorted(roots)

    def run(self) -> dict:
        for check in (self.check_instruction_links, self.check_repo_skills_link, self.check_global_skill_links,
                      self.check_config_dirs, self.check_skill_placement, self.check_skill_refs,
                      self.check_skill_duplicates, self.check_skill_frontmatter, self.check_context_budget,
                      self.check_agent_files, self.check_codex, self.check_hooks,  # these add to the inventory
                      self.check_mcp, self.check_mcp_secrets, self.check_permissions, self.check_risky_settings,
                      self.check_global_leak, self.check_doc_refs, self.check_memory, self.check_other_agents,
                      self.check_hygiene, self.check_stale_names, self.check_mcp_duplicates, self.check_config_invalid):
            check()
        order = {"error": 0, "warn": 1, "info": 2}
        self.findings.sort(key=lambda f: (order[f["severity"]], f["code"], f["where"]))
        return {
            "repo": str(self.repo),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "inventory": self.inventory,
            "findings": self.findings,
        }


# -- module helpers ----------------------------------------------------------

def load_json(path: Path, errors: list | None = None) -> dict:
    """The file's top-level object, or {} when it is missing or broken. A broken one is noted in
    `errors` with the parse position only, never its content (it can hold keys)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        if errors is not None:
            where = f" at line {e.lineno} column {e.colno}" if isinstance(e, json.JSONDecodeError) else ""
            errors.append((path, f"not valid JSON{where}"))
        return {}
    if not isinstance(data, dict):
        if errors is not None:
            errors.append((path, f"top level is a {type(data).__name__}, not an object"))
        return {}
    return data


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def walk_dirs(base: Path):
    """Every folder under `base`, skipping SKIP_DIRS (rglob cannot prune, so it walks node_modules)."""
    for dirpath, dirnames, _ in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        yield Path(dirpath)


def walk_files(base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield dirpath, name


def exists_below(base: Path, ref: str) -> bool:
    """True when `ref` exists relative to `base` or to any folder below it."""
    return any((d / ref).exists() for d in walk_dirs(base))


def skill_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if not p.name.startswith(".") and (p / "SKILL.md").exists())


def frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            meta[key] = "" if value in (">", "|", ">-", "|-") else value.strip("\"'")
        elif key and line.startswith((" ", "\t")):
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta


def markdown_links(text: str) -> list[str]:
    # A link inside a code span or block is an example to write elsewhere, not a pointer.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    links = re.findall(r"\]\(([^)\s#]+)(?:#[^)]*)?\)", text)
    return [l for l in links if not re.match(r"^[a-z]+:", l) and not l.startswith("/")]


def skill_path_refs(text: str) -> list[str]:
    """Paths a SKILL.md points the agent at: inline code, and scripts or folders in shell blocks.

    A data file may be an output that does not exist yet, so it counts as dead only when even its parent
    folder is missing (a "?" prefix marks it). Scripts and folders must exist themselves.
    """
    refs = set()
    for r in re.findall(r"`([^`\s]+)`", text):
        if looks_like_path(r, dirs=True):
            refs.add(r if r.endswith(("/", ".py", ".sh")) else "?" + r)
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        tokens = block.replace("\\\n", " ").split()
        for i, tok in enumerate(tokens):
            tok = tok.strip("\"'")
            if not looks_like_path(tok, dirs=True):
                continue
            ran = i > 0 and re.match(r"^(python3?|bash|sh|node|dart|\S*/python3?)$", tokens[i - 1])
            refs.add(tok if ran else "?" + tok)
    return sorted(refs)


SCRATCH_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


def looks_like_path(ref: str, dirs: bool = False) -> bool:
    if re.match(r"^[a-z]+:", ref) or any(c in ref for c in "*<>{}$|=,()") or ref.startswith(("-", "@", ".claude.json")):
        return False
    if ref.startswith(SCRATCH_PREFIXES):  # temp and scratchpad folders come and go; never a dead ref
        return False
    if dirs and ref.endswith("/") and ref.count("/") >= 2:
        return True
    return "/" in ref.rstrip("/") and bool(re.search(r"\.(md|json|jsonl|ya?ml|py|sh|dart|toml|txt|csv|arb)$", ref))


def redact(text: str) -> tuple[str, list[str]]:
    """Replace every secret in `text` with `<redacted:KIND>`; return the text and the kinds found.

    No character of a secret survives, so the result is safe to print and to save."""
    kinds: list[str] = []
    for kind, rx in SECRET_PATTERNS:
        def sub(m: re.Match, kind=kind) -> str:
            if kind not in kinds:
                kinds.append(kind)
            if "secret" in rx.groupindex and m.group("secret") is not None:
                start, end = m.span("secret")
                return m.string[m.start():start] + f"<redacted:{kind}>" + m.string[end:m.end()]
            return f"<redacted:{kind}>"
        text = rx.sub(sub, text)
    return text, kinds


def literal_value(v) -> bool:
    """A value that can be a credential: `${VAR}` references (also inside `Bearer ${VAR}`), paths and URLs are not."""
    return (isinstance(v, str) and len(v.strip()) >= 8 and not v.strip().startswith(("$", "/", "~", "http"))
            and not re.search(r"\$\{?[A-Za-z_]", v))


def obj(x) -> dict:
    """A config node that should be an object; anything else counts as empty."""
    return x if isinstance(x, dict) else {}


def strs(x) -> list[str]:
    """The string items of a config list; a missing or odd-typed node counts as empty."""
    return [r for r in x if isinstance(r, str)] if isinstance(x, list) else []


def same_tree(a: Path, b: Path) -> bool:
    files = lambda root: sorted(str(Path(d, n).relative_to(root)) for d, n in walk_files(root))
    try:
        if files(a) != files(b):
            return False
        return all(filecmp.cmp(a / f, b / f, shallow=False) for f in files(a))
    except OSError:  # a broken link inside counts as a difference: never suggest deleting it unseen
        return False


def inline_secrets(spec: dict) -> list[str]:
    """Where an MCP server spec holds a credential: header, env and top-level keys named like one
    (names only, never values), plus any known token shape anywhere in it."""
    literal = literal_value
    kinds = []
    for sub in ("headers", "http_headers", "env"):
        node = spec.get(sub)
        for k, v in (node.items() if isinstance(node, dict) else []):
            if CRED_KEY.search(str(k)) and literal(v):
                kinds.append(f"{sub}.{k}")
    for k, v in spec.items():
        if not k.startswith("_") and CRED_KEY.search(k) and not k.endswith(("env_var", "_env")) and literal(v):
            kinds.append(k)
    _, found = redact(json.dumps({k: v for k, v in spec.items() if not k.startswith("_")}))
    return kinds + [k for k in dict.fromkeys(found) if not kinds]


def walk_hooks(node, path: tuple = ()):
    """(event, command) for every command hook, in Claude's, Codex's and Antigravity's hook layouts.
    The event is the path of object keys to it, without the `hooks` wrappers."""
    if isinstance(node, dict):
        command = node.get("command")
        if isinstance(command, str) and node.get("type", "command") == "command":
            yield "/".join(path), command
            return
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                yield from walk_hooks(v, path if k == "hooks" else path + (k,))
    elif isinstance(node, list):
        for v in node:
            yield from walk_hooks(v, path)


def imports_agents_md(text: str) -> bool:
    """CLAUDE.md that loads AGENTS.md through an `@AGENTS.md` import line (Claude-only lines may follow)."""
    return any(line.strip() in ("@AGENTS.md", "@./AGENTS.md") for line in text.splitlines())


def instruction_imports(text: str) -> list[str]:
    """`@path` imports outside code. A ref needs a file extension or a ./, ../, ~/ or / start, which
    leaves out mentions, e-mail addresses and npm scopes such as @anthropic-ai/sdk."""
    text = re.sub(r"^(```|~~~).*?^\1", "", text, flags=re.M | re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    refs = []
    for m in re.finditer(r"(?<![\w@./-])@((?:~/|\.{0,2}/)?[\w.-]+(?:/[\w.-]+)*)", text):
        ref = m.group(1).rstrip(".")
        if (re.search(r"\.\w+$", ref) or ref.startswith(("~/", "./", "../", "/"))) and ref not in refs:
            refs.append(ref)
    return refs


def git_remote_slug(repo: Path) -> str:
    url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


# -- run history ---------------------------------------------------------------

RUN_FILE = re.compile(r"^\d{8}-\d{6}\.json$")


def runs_dir_for(home: Path, repo: Path, write: bool) -> Path:
    """`~/.agents/lint-runs/<name>-<hash of path>`, so same-named repos and clones keep apart.

    Runs saved before this lived in `<name>/`. If that folder belongs to this repo (its runs name this
    path), a write moves it to the new place; a read-only run reads it where it is and changes nothing."""
    root = home / ".agents/lint-runs"
    new = root / f"{repo.name}-{hashlib.sha1(str(repo).encode()).hexdigest()[:6]}"
    legacy = root / repo.name
    if new.exists() or not legacy.is_dir():
        return new
    runs = sorted(p for p in legacy.glob("*.json") if RUN_FILE.match(p.name))
    if not runs or load_json(runs[-1]).get("repo") != str(repo):  # no proof it is this repo's folder
        return new
    if not write:
        return legacy
    legacy.rename(new)
    return new


def finding_id(fingerprint: str) -> str:
    return hashlib.sha1(fingerprint.encode()).hexdigest()[:8]


def compare_with_previous(result: dict, runs_dir: Path) -> dict:
    # Only timestamped run files count; accepted.json lives in the same folder and sorts after them.
    previous = sorted(p for p in runs_dir.glob("*.json") if RUN_FILE.match(p.name))
    old = load_json(previous[-1]) if previous else {}
    old_prints = {f["fingerprint"] for f in old.get("findings", [])}
    # Runs saved before explicit keys fingerprinted keyless findings by message; match those too.
    new_prints = {f["fingerprint"] for f in result["findings"]}.union(*(legacy_fingerprints(f) for f in result["findings"]))
    for f in result["findings"]:
        known = f["fingerprint"] in old_prints or bool(legacy_fingerprints(f) & old_prints)
        f["status"] = "persisting" if known else "new"
    result["resolved"] = [f for f in old.get("findings", []) if f["fingerprint"] not in new_prints]
    result["previous_run"] = previous[-1].name if previous else None
    return result


KEY_PREFIXES = ("mcp:", "path:", "long:")


def legacy_fingerprints(f: dict) -> set[str]:
    """Fingerprints older runs gave this finding: by message when it had no key, and perm-stale keys
    without their `mcp:`/`path:`/`long:` prefix."""
    prints = {f"{f.get('code')}|{f.get('where')}|{f.get('message')}"}
    code, where, key = (f.get("fingerprint") or "||").split("|", 2)
    if key.startswith(KEY_PREFIXES):
        prints.add(f"{code}|{where}|{key.split(':', 1)[1]}")
    return prints


def apply_accepted(result: dict, accepted_file: Path) -> dict:
    """Move findings the user accepted (fingerprint -> reason) out of the counts."""
    accepted = load_json(accepted_file)

    def reason(f: dict):
        for fp in [f["fingerprint"], *sorted(legacy_fingerprints(f))]:
            if fp in accepted:
                return accepted[fp]
        return None

    result["accepted"] = [dict(f, reason=reason(f)) for f in result["findings"] if reason(f) is not None]
    result["findings"] = [f for f in result["findings"] if reason(f) is None]
    return result


def accept(result: dict, accepted_file: Path, target: str, reason: str, prefix: bool = False) -> list[str]:
    """Record the finding whose `id` or full fingerprint is `target`.

    With `prefix=True`, record every finding whose fingerprint starts with `target`."""
    accepted = load_json(accepted_file)
    if prefix:
        hits = [f["fingerprint"] for f in result["findings"] if f["fingerprint"].startswith(target)]
    else:
        hits = [f["fingerprint"] for f in result["findings"] if target in (f["id"], f["fingerprint"])]
    for fp in hits:
        accepted[fp] = reason
    if hits:
        accepted_file.parent.mkdir(parents=True, exist_ok=True)
        accepted_file.write_text(json.dumps(accepted, ensure_ascii=False, indent=2) + "\n")
    return hits


def print_text(result: dict):
    inv = result["inventory"]
    print(f"repo: {result['repo']}  remote: {inv.get('remote') or '-'}")
    print(f"skills listed to Claude: {inv.get('skills_listed')} (≈{inv.get('skill_listing_tokens_est')} tokens)  "
          f"connectors: {inv.get('claude_ai_connectors')}  claude.ai skill sync: {inv.get('claude_ai_skill_sync')}")
    for s in inv.get("mcp_servers", []):
        print(f"  mcp {s['name']:<14} {s['source']:<26} {'enabled' if s['enabled'] else 'disabled'}")
    marks = {"new": "NEW ", "persisting": "    "}
    for f in result["findings"]:
        print(f"{marks.get(f.get('status'), '')}{f['id']} [{f['severity']}] {f['code']} {f['where']}: {f['message']}")
    for f in result.get("resolved", []):
        print(f"FIXED [{f['severity']}] {f['code']} {f['where']}: {f['message']}")
    for f in result.get("accepted", []):
        print(f"ACCEPTED {f['code']} {f['where']}: {f['message']} — {f['reason']}")
    counts = {s: sum(f["severity"] == s for f in result["findings"]) for s in ("error", "warn", "info")}
    print(f"\n{counts['error']} error, {counts['warn']} warn, {counts['info']} info")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".", help="repo to lint (default: current git root)")
    ap.add_argument("--home", default=str(Path.home()), help="home directory (tests point this at a fixture)")
    ap.add_argument("--config", help=f"config file (default: ~/{CONFIG_FILE})")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--save", action="store_true", help="store this run in ~/.agents/lint-runs/<repo>-<hash>/")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--accept", metavar="ID",
                       help="accept the one finding with this id (or full fingerprint); needs --reason")
    group.add_argument("--accept-prefix", metavar="PREFIX",
                       help="accept every finding whose fingerprint (`code|where|key`) starts with PREFIX; "
                            "prints each match; needs --reason")
    ap.add_argument("--reason", help="why the finding is accepted, shown on every later run")
    args = ap.parse_args()

    if (args.accept or args.accept_prefix) and not args.reason:
        ap.error("--accept and --accept-prefix need --reason")
    start = Path(args.repo).resolve()
    if not start.is_dir():
        ap.error(f"--repo {args.repo} is not a directory")
    top = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    repo = Path(top.stdout.strip()) if top.returncode == 0 else start
    home = Path(args.home).resolve()

    result = Linter(repo, home, Path(args.config).expanduser() if args.config else None).run()
    runs_dir = runs_dir_for(home, repo, write=bool(args.save or args.accept or args.accept_prefix))
    result = compare_with_previous(result, runs_dir)
    if args.accept or args.accept_prefix:
        hits = accept(result, runs_dir / "accepted.json", args.accept or args.accept_prefix, args.reason,
                      prefix=bool(args.accept_prefix))
        print(f"accepted {len(hits)} finding(s):", file=sys.stderr)
        for fp in hits:
            print(f"  {finding_id(fp)}  {fp}", file=sys.stderr)
        if not hits:
            sys.exit("no finding matches; check the id against the last report")
    result = apply_accepted(result, runs_dir / "accepted.json")
    if args.save:
        runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        (runs_dir / f"{stamp}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_text(result)


if __name__ == "__main__":
    main()
