"""The skills the factory hands its agents: a project's own, read from the job's worktree so the
branch's version applies (from the main checkout before a job exists), or one of the factory's
defaults (defaults/skills/). [heroes] gives the builder, the tidy pass and the wiki pass theirs;
[mages] gives each Mage its rulebook; [intake] gives /factory add its own."""

from .errors import FactoryError

HERO_OF_ACTION = {"spawn-builder": "coder", "spawn-tidy": "scout", "spawn-wiki": "writer"}


def path(paths, job_id, ref):
    if ref["kind"] == "skill":
        checkout = paths.workspace(job_id) if job_id else paths.root
        found = checkout / ".claude" / "skills" / ref["name"] / "SKILL.md"
    else:
        found = paths.plugin / "defaults" / "skills" / ref["name"] / "SKILL.md"
    if not found.is_file():
        raise FactoryError(f"{ref['kind']}:{ref['name']} has no skill at {found}")
    return found


def intake(paths, cfg):
    """The skills /factory add reads before a job exists, as paths, or None where the project names none."""
    return {key: str(path(paths, None, ref)) if ref else None for key, ref in cfg.intake.items()}


def hero(paths, cfg, job_id, name):
    """The skills a hero follows in this job, as paths; a missing one is an error, never skipped."""
    return [path(paths, job_id, ref) for ref in cfg.heroes[name]]
