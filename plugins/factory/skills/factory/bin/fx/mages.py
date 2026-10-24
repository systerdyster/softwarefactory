"""Mages: the reviewers a project picks in [mages], each reviewing by a rulebook — a skill of the
project's or one of the factory's default review-<name> rulebooks (see skills.py). The rulebook's
`model:` front matter picks the model the Mage runs on.

A project's rulebook is read from the target branch, never from the job's branch: the builder writes
anywhere in the worktree, and the change under review must not be able to rewrite the rules it is
reviewed by. It is copied into the job folder, where only the factory writes, for the Mage to read."""

import re

from . import gitutil, jobs, skills
from .config import REQUIRED_REVIEWERS
from .errors import FactoryError

DEFAULT_MODEL = "sonnet"
MAIN = REQUIRED_REVIEWERS[0]


def _book(cfg, name):
    book = cfg.mages.get(name)
    if not book:
        raise FactoryError(f"{name} is not a Mage in project.md's [mages]")
    if book["kind"] == "codex":
        raise FactoryError("codex reviews through `codex-review`; its fallback has its own agent")
    return book


def _text(paths, cfg, job_id, name):
    """The rulebook's text, and the factory's file it is when it is a default (None for a project's)."""
    book = _book(cfg, name)
    if book["kind"] == "default":
        try:
            found = skills.path(paths, job_id, {"kind": "default", "name": f"review-{book['name']}"})
        except FactoryError as e:
            raise FactoryError(f"[mages] {name}: no rulebook — {e}")
        return jobs.read(found), found
    rel = f".claude/skills/{book['name']}/SKILL.md"
    text = gitutil.show(cfg.base_ref, rel, paths.root)
    if text is None:
        raise FactoryError(f"[mages] {name}: no rulebook — {rel} is not on {cfg.base_ref}, the target branch")
    return text, None


def rulebook(paths, cfg, job_id, name):
    """The file the Mage reads: the factory's default in place, or the project's skill as the target has
    it, copied into the job folder."""
    text, default = _text(paths, cfg, job_id, name)
    if default:
        return default
    out = paths.job(job_id) / f"rulebook-{name}.md"
    jobs.write_atomic(out, text)
    return out


def model(text):
    head = (text or "").split("\n---", 1)[0]
    found = re.search(r"^model:\s*(\w+)\s*$", head, re.M)
    return found.group(1) if found else DEFAULT_MODEL


def models(paths, cfg, job_id, names):
    """The model each Mage of a round runs on; codex, through its CLI, has none."""
    return {name: model(_text(paths, cfg, job_id, name)[0]) for name in names
            if cfg.mages.get(name, {}).get("kind") != "codex"}
