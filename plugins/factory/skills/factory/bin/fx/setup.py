"""`init-files` and `allowlist`: the mechanical half of /factory init. Inspecting the repo and
writing project.md is judgement and stays with the session running init."""

import json
import re

from . import jobs
from .errors import FactoryError

READ_GIT = ["status", "diff", "log", "show", "merge-base", "rev-parse", "ls-files", "blame", "grep", "cat-file"]
WRITE_GIT = ["add", "rm", "mv", "commit", "restore"]


def _strip_frontmatter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


def init_files(paths):
    created = []
    for d in (paths.factory, paths.jobs, paths.inbox, paths.inbox_done, paths.logs):
        if not d.exists():
            d.mkdir(parents=True)
            created.append(paths.relative(d) + "/")
    if not paths.project.exists():
        template = paths.skill / "reference" / "project-template.md"
        paths.project.write_text(_strip_frontmatter(template.read_text(encoding="utf-8")), encoding="utf-8")
        created.append(paths.relative(paths.project))
    if not paths.backlog.exists():
        paths.backlog.write_text("# Backlog\n\nApproved jobs, oldest first. Written only through `factory.py queue`.\n\n",
                                 encoding="utf-8")
        created.append(paths.relative(paths.backlog))
    gitignore = paths.root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    ignored = bool(re.search(r"^/?factory/?\s*$", text, re.M))
    if not ignored:
        gitignore.write_text(text.rstrip("\n") + ("\n\n" if text.strip() else "")
                             + "# Software factory runtime state: jobs, workspace, board. Never committed.\n/factory/\n",
                             encoding="utf-8")
        created.append(".gitignore: /factory/")
    return {"created": created, "project": paths.relative(paths.project)}


def proposed_rules():
    allow = ["Bash(python3 .claude/skills/factory/bin/factory.py:*)"]
    allow += [f"Bash(git -C factory/workspace {sub}:*)" for sub in READ_GIT + WRITE_GIT]
    allow += ["Bash(ls:*)", "Edit(/factory/**)"]
    return allow


def allowlist(paths, cfg, write=False):
    settings_file = paths.root / ".claude" / "settings.json"
    settings = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise FactoryError(f".claude/settings.json is not valid JSON: {e}")
    rules = proposed_rules()
    current = settings.get("permissions", {}).get("allow", [])
    missing = [r for r in rules if r not in current]
    result = {"file": paths.relative(settings_file), "add": missing, "already_present": len(rules) - len(missing),
              "note": "git push is deliberately absent: only the orchestrator pushes, through factory.py pr."}
    if write and missing:
        settings.setdefault("permissions", {}).setdefault("allow", [])
        settings["permissions"]["allow"] = current + missing
        jobs.write_atomic(settings_file, json.dumps(settings, indent=2) + "\n")
        result["written"] = True
    return result
