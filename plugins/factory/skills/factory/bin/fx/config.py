"""Reads the machine-readable part of factory/project.md: the fenced ```ini block under
"## Configuration". Everything else in project.md is prose for the agents."""

import configparser
import re

from .errors import FactoryError

ALWAYS_REVIEWERS = ["security", "code", "performance", "stability"]
OPTIONAL_REVIEWERS = ["platform", "ux", "ui", "codex"]
ALWAYS_ON_CODE_CHANGE = ["security", "code", "codex"]
MERGE_MODES = ["pr", "local-merge", "auto-merge"]
PR_TOOLS = ["gh", "az", "other"]
JIRA_LINKS = ["title", "body", "both", "none"]
BRANCH_FIELDS = {"id", "key", "slug"}

_BLOCK = re.compile(r"^```ini[^\n]*\n(.*?)^```", re.S | re.M)


class Config:
    def __init__(self, parser):
        self._p = parser

    def get(self, section, key, default=None):
        if not self._p.has_option(section, key):
            return default
        value = self._p.get(section, key).strip()
        return value if value != "" else default

    def getint(self, section, key, default):
        value = self.get(section, key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise FactoryError(f"project.md: [{section}] {key} must be a number, got {value!r}")

    def getbool(self, section, key, default=False):
        value = self.get(section, key)
        if value is None:
            return default
        return value.lower() in ("true", "yes", "on", "1")

    def getlist(self, section, key):
        value = self.get(section, key)
        if not value:
            return []
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]

    def getlines(self, section, key):
        value = self.get(section, key)
        if not value:
            return []
        return [line.strip() for line in value.splitlines() if line.strip()]

    @property
    def target_branch(self):
        return self.get("factory", "target_branch", "main")

    @property
    def remote(self):
        value = self.get("factory", "remote", "origin")
        return "" if value in ("none", "-") else value

    @property
    def base_ref(self):
        return f"{self.remote}/{self.target_branch}" if self.remote else self.target_branch

    @property
    def merge_mode(self):
        mode = self.get("factory", "merge_mode", "pr")
        if mode not in MERGE_MODES:
            raise FactoryError(f"project.md: merge_mode must be one of {MERGE_MODES}, got {mode!r}")
        return mode

    @property
    def pr_tool(self):
        tool = self.get("factory", "pr_tool", "gh")
        if tool not in PR_TOOLS:
            raise FactoryError(f"project.md: pr_tool must be one of {PR_TOOLS}, got {tool!r}")
        return tool

    @property
    def max_rounds(self):
        return self.getint("factory", "max_rounds", 3)

    @property
    def max_diff(self):
        return self.getint("factory", "max_diff", 800)

    @property
    def suite_timeout(self):
        return self.getint("factory", "suite_timeout", 1800)

    def command(self, name):
        return self.get("commands", name)

    def command_names(self):
        if not self._p.has_section("commands"):
            return []
        return list(self._p.options("commands"))

    @property
    def setup_commands(self):
        return self.getlines("workspace", "setup")

    @property
    def lockfiles(self):
        return self.getlist("workspace", "lockfiles")

    @property
    def enabled_reviewers(self):
        optional = [r for r in self.getlist("reviewers", "enabled") if r in OPTIONAL_REVIEWERS]
        unknown = [r for r in self.getlist("reviewers", "enabled") if r not in OPTIONAL_REVIEWERS + ALWAYS_REVIEWERS]
        if unknown:
            raise FactoryError(f"project.md: unknown reviewers in [reviewers] enabled: {unknown}")
        return ALWAYS_REVIEWERS + [r for r in OPTIONAL_REVIEWERS if r in optional]

    def path_patterns(self, reviewer):
        return self.getlist("paths", reviewer)

    @property
    def branch_pattern(self):
        pattern = self.get("handoff", "branch", "factory/{id}")
        fields = set(re.findall(r"\{(\w+)\}", pattern))
        if fields - BRANCH_FIELDS:
            raise FactoryError(f"project.md: [handoff] branch may use {sorted(BRANCH_FIELDS)}, got {sorted(fields)}")
        if not fields & {"id", "key"}:
            raise FactoryError("project.md: [handoff] branch must contain {id} or {key} so every job gets its own branch")
        return pattern

    @property
    def pr_draft(self):
        return self.getbool("handoff", "draft")

    @property
    def pr_reviewers(self):
        return self.getlist("handoff", "reviewers")

    @property
    def pr_required_reviewers(self):
        return self.getlist("handoff", "required_reviewers")

    @property
    def jira_link(self):
        mode = self.get("handoff", "jira_link", "title")
        if mode not in JIRA_LINKS:
            raise FactoryError(f"project.md: [handoff] jira_link must be one of {JIRA_LINKS}, got {mode!r}")
        return mode

    @property
    def jira_url(self):
        return self.get("handoff", "jira_url")

    @property
    def link_work_items(self):
        return self.getbool("handoff", "work_items")

    @property
    def pr_language(self):
        return self.get("handoff", "language", "en")

    @property
    def pr_template(self):
        return self.get("handoff", "pr_template")

    @property
    def jira_enabled(self):
        return (self.get("jira", "enabled", "false") or "").lower() in ("true", "yes", "on", "1")

    def jira_status(self, stage):
        return self.get("jira", stage)


def parse_text(text):
    match = _BLOCK.search(text)
    if not match:
        raise FactoryError("project.md has no ```ini configuration block")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str.lower
    try:
        parser.read_string(match.group(1))
    except configparser.Error as e:
        raise FactoryError(f"project.md configuration block does not parse: {e}")
    return Config(parser)


def load(paths):
    if not paths.project.exists():
        raise FactoryError(f"{paths.relative(paths.project)} is missing; run /factory init first")
    return parse_text(paths.project.read_text(encoding="utf-8"))
