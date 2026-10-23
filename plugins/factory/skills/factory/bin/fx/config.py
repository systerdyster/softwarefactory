"""Reads the machine-readable part of factory/project.md: the fenced ```ini block under
"## Configuration". Everything else in project.md is prose for the agents."""

import configparser
import re

from .errors import FactoryError

REQUIRED_REVIEWERS = ["code"]
SPECIALISTS = ["security", "performance", "stability"]
OPTIONAL_REVIEWERS = ["platform", "ux", "ui", "codex"]
MERGE_MODES = ["pr", "local-merge", "auto-merge"]
PR_TOOLS = ["gh", "az", "other"]
JIRA_LINKS = ["title", "body", "both", "none"]
BRANCH_FIELDS = {"id", "key", "slug"}
DEFAULT_COMMITS = ("Subject imperative and short, no trailing period (`Add user profile route`, not `Added …` or a "
                   "noun phrase). A body only when the why isn't obvious from subject and diff, then 2–3 lines on the "
                   "reason, never a paraphrase of the diff. No case key in the subject when the branch name carries it. "
                   "Related work in one commit rather than many small ones.")
DEFAULT_COMMENTS = ("Write none by default: names and small functions say what the code does. Add a comment only "
                    "where the why is a trap the code cannot show, and check the trap is real. No comment inside a "
                    "function body narrating it, no doc comment on every export or class, no comment on each prop "
                    "of a props type, no paragraph introducing a file or a variant. Keep to the comment style of "
                    "the surrounding file (section headers it already uses stay), and don't strip comments from "
                    "code you aren't otherwise changing.")
DEFAULT_WRITING = ("Frame it plainly first — what the thing is and why it exists, for a capable reader new to this "
                   "domain, without talking down — then name the exact ingredients (real files, classes, routes, "
                   "settings, steps in the order they happen), then the gotcha that saves the next developer an "
                   "hour. Name an ingredient precisely; never paste its mechanics: no method bodies, no CSS dumps, "
                   "no root-cause forensics. Current state in the present tense — no \"previously\", \"renamed "
                   "from\", \"now\", version numbers, commit hashes or case keys. A fact that needs upkeep lives on "
                   "one page and the others link to it. Write in the page's language and its neighbours'; use the "
                   "customer's own names for their things, and quote code identifiers and UI labels verbatim.")

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

    @property
    def parallel(self):
        """Active build slots: jobs building, reviewing or rebasing at once. 1 or more."""
        value = self.getint("factory", "parallel", 2)
        if value < 1:
            raise FactoryError(f"project.md: [factory] parallel must be 1 or more, got {value}")
        return value

    @property
    def server_command(self):
        """A dev server the factory starts on $FACTORY_PORT for the commands in [server] for."""
        return self.get("server", "command")

    @property
    def server_for(self):
        return self.getlist("server", "for")

    @property
    def server_timeout(self):
        return self.getint("server", "timeout", 120)

    @property
    def serial_commands(self):
        """[commands] names that write shared data (migrations, seeding, tests against a shared
        database) and so run one at a time across jobs."""
        return self.getlist("factory", "serial")

    def command(self, name):
        return self.get("commands", name)

    def command_names(self):
        if not self._p.has_section("commands"):
            return []
        return list(self._p.options("commands"))

    @property
    def verify_commands(self):
        """The [commands] names that together run the project's tests, build and lint. Set
        [factory] verify when one command already covers another; the default runs lint, build and
        suite, each where it is defined."""
        names = self.getlist("factory", "verify") or [n for n in ("lint", "build", "suite") if self.command(n)]
        missing = [n for n in names if not self.command(n)]
        if missing:
            raise FactoryError(f"project.md: [factory] verify names commands [commands] does not define: {missing}")
        if not names:
            raise FactoryError("project.md defines none of lint, build and suite, and no [factory] verify")
        return names

    @property
    def setup_commands(self):
        return self.getlines("workspace", "setup")

    @property
    def lockfiles(self):
        return self.getlist("workspace", "lockfiles")

    @property
    def enabled_reviewers(self):
        """Every reviewer that may run: the main reviewer, the specialists, and the optional ones the
        project enabled. Which of them a round runs is schedule's call."""
        listed = self.getlist("reviewers", "enabled")
        unknown = [r for r in listed if r not in REQUIRED_REVIEWERS + SPECIALISTS + OPTIONAL_REVIEWERS]
        if unknown:
            raise FactoryError(f"project.md: unknown reviewers in [reviewers] enabled: {unknown}")
        return REQUIRED_REVIEWERS + SPECIALISTS + [r for r in OPTIONAL_REVIEWERS if r in listed]

    @property
    def reviewer_commands(self):
        """Project commands reviewers may run, e.g. the browser tests that render components."""
        return self.getlist("reviewers", "commands")

    @property
    def docs(self):
        """Globs of the project's written rules beyond CLAUDE.md/AGENTS.md, e.g. Wiki/*.md."""
        return self.getlist("rules", "docs")

    @property
    def wiki_globs(self):
        """Globs of the wiki pages inside the repo that the wiki agent may edit, e.g. Wiki/Tech/**/*.md.
        Empty: the project has no in-repo wiki and the wiki agent never runs."""
        return self.getlist("rules", "wiki")

    @property
    def wiki_external(self):
        """Wikis that live in other repos: named in the wiki agent's report, never edited."""
        return self.getlist("rules", "wiki_external")

    @property
    def wiki_guide(self):
        """The project's own wiki conventions (a file in the repo) that the wiki agent follows, if any."""
        return self.get("rules", "wiki_guide")

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
    def auto_complete_available(self):
        """Auto-complete is the PR tool's own setting, so it needs a PR tool and a PR."""
        return self.pr_tool in ("gh", "az") and self.merge_mode in ("pr", "auto-merge")

    @property
    def test_note_language(self):
        return self.get("handoff", "test_note_language") or self.pr_language

    @property
    def pr_template(self):
        return self.get("handoff", "pr_template")

    @property
    def comments(self):
        """When agents write code comments: the project's rule, else DEFAULT_COMMENTS."""
        return self.get("rules", "comments") or DEFAULT_COMMENTS

    @property
    def writing(self):
        """How agents write documentation — wiki pages, READMEs: the project's rule, else DEFAULT_WRITING."""
        return self.get("rules", "writing") or DEFAULT_WRITING

    @property
    def commits(self):
        """How agents write commit messages: the project's convention, else DEFAULT_COMMITS."""
        return self.get("handoff", "commits") or DEFAULT_COMMITS

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
