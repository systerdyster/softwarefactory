"""Mages: the reviewers a project picks in [mages], each reviewing by a rulebook — a skill of the
project's, read from the job's worktree so the branch's version applies, or one of the factory's
defaults. The rulebook's `model:` front matter picks the model the Mage runs on."""

import re

from . import jobs
from .config import REQUIRED_REVIEWERS
from .errors import FactoryError

DEFAULT_MODEL = "sonnet"
MAIN = REQUIRED_REVIEWERS[0]


def rulebook(paths, cfg, job_id, name):
    book = cfg.mages.get(name)
    if not book:
        raise FactoryError(f"{name} is not a Mage in project.md's [mages]")
    if book["kind"] == "codex":
        raise FactoryError("codex reviews through `codex-review`; its fallback has its own agent")
    if book["kind"] == "skill":
        path = paths.workspace(job_id) / ".claude" / "skills" / book["name"] / "SKILL.md"
    else:
        path = paths.plugin / "defaults" / "skills" / f"review-{book['name']}" / "SKILL.md"
    if not path.is_file():
        raise FactoryError(f"[mages] {name}: no rulebook at {path}")
    return path


def model(path):
    head = (jobs.read(path) or "").split("\n---", 1)[0]
    found = re.search(r"^model:\s*(\w+)\s*$", head, re.M)
    return found.group(1) if found else DEFAULT_MODEL


def models(paths, cfg, job_id, names):
    """The model each Mage of a round runs on; codex, through its CLI, has none."""
    return {name: model(rulebook(paths, cfg, job_id, name)) for name in names
            if cfg.mages.get(name, {}).get("kind") != "codex"}
