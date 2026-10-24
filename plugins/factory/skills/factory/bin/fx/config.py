"""Reads the machine-readable part of factory/project.md: the fenced ```ini block under
"## Configuration". Everything else in project.md is prose for the agents."""

import configparser
import re

from .errors import FactoryError

REQUIRED_REVIEWERS = ["code"]
MERGE_MODES = ["pr", "local-merge", "auto-merge"]
PR_TOOLS = ["gh", "az", "other"]
CASE_LINKS = ["title", "body", "both", "none"]
HEROES = ["coder", "scout", "writer"]
# Settings an older factory read; refused rather than ignored, so a project is never half configured.
# reference/init.md has what each became.
LEGACY = {("jira", None): "[events]", ("reviewers", "enabled"): "[mages]", ("rules", "wiki_guide"): "[heroes] writer",
          ("handoff", "jira_link"): "case_link", ("handoff", "jira_url"): "case_url"}
EVENTS = ["claimed", "started", "waiting", "handed-off", "completed", "cancelled"]
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
    def mages(self):
        """Every Mage that may review, name → the rulebook it reviews by: `skill:<name>` (a skill of the
        project's), `default:<name>` (the factory's defaults/skills/review-<name>) or `codex` (the Codex
        CLI). `code` is the main Mage and reviews every job; which others a round runs is schedule's
        call."""
        if not self._p.has_section("mages"):
            raise FactoryError("project.md has no [mages]; run /factory init to add it")
        result = {name: _rulebook(name, value.strip()) for name, value in self._p.items("mages")}
        missing = [r for r in REQUIRED_REVIEWERS if r not in result]
        if missing:
            raise FactoryError(f"project.md: [mages] needs {missing[0]}, the main Mage that reviews every job")
        return result

    @property
    def heroes(self):
        """The skills each hero follows beside its own method, from [heroes]: coder (the builder), scout
        (the tidy pass) and writer (the wiki pass), each a list of skill:<name> or default:<name>."""
        if self._p.has_section("heroes"):
            unknown = [k for k, _ in self._p.items("heroes") if k not in HEROES]
            if unknown:
                raise FactoryError(f"project.md: [heroes] knows {HEROES}, not {unknown}")
        return {h: [_skill_ref(f"[heroes] {h}", v) for v in self.getlist("heroes", h)] for h in HEROES}

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
    def case_link(self):
        mode = self.get("handoff", "case_link", "title")
        if mode not in CASE_LINKS:
            raise FactoryError(f"project.md: [handoff] case_link must be one of {CASE_LINKS}, got {mode!r}")
        return mode

    @property
    def case_url(self):
        return self.get("handoff", "case_url")

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

    def event_handler(self, event):
        """What [events] runs when a job reaches `event`: a shell command, or `skill:<name> [args]` for
        the session to run with its own tools (a tracker reached only through them); None when the
        project does nothing then. The factory knows no tracker: what an event means is the project's."""
        if event not in EVENTS:
            raise FactoryError(f"unknown event {event!r}; one of {EVENTS}")
        value = self.get("events", event)
        if not value:
            return None
        if value.startswith("skill:"):
            name, _, args = value[len("skill:"):].strip().partition(" ")
            if not name:
                raise FactoryError(f"project.md: [events] {event} names no skill")
            return {"kind": "skill", "skill": name, "args": args.strip()}
        return {"kind": "command", "command": value}


def _rulebook(name, value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise FactoryError(f"project.md: [mages] {name!r} is not a name (lower-case letters, digits, dashes)")
    if (value == "codex") != (name == "codex"):
        raise FactoryError(f"project.md: [mages] {name} = {value!r}; codex, and only codex, is the Codex CLI")
    if value == "codex":
        return {"kind": "codex"}
    return _skill_ref(f"[mages] {name}", value, "or codex")


def _skill_ref(where, value, alternative=""):
    kind, _, skill = value.partition(":")
    if kind not in ("skill", "default") or not re.fullmatch(r"[A-Za-z0-9][\w.-]*", skill.strip()):
        raise FactoryError(f"project.md: {where} = {value!r}; expected skill:<name> or default:<name> {alternative}".rstrip())
    return {"kind": kind, "name": skill.strip()}


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
    old = [(f"[{s}] {k}" if k else f"[{s}]") + f" → {new}" for (s, k), new in LEGACY.items()
           if (parser.has_option(s, k) if k else parser.has_section(s))]
    if old:
        raise FactoryError(f"project.md is from an older factory ({', '.join(old)}); run /factory init, which "
                           "proposes the new form for you to approve")
    return Config(parser)


def load(paths):
    if not paths.project.exists():
        raise FactoryError(f"{paths.relative(paths.project)} is missing; run /factory init first")
    return parse_text(paths.project.read_text(encoding="utf-8"))
