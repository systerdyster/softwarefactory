"""Where everything lives. The factory runs as a plugin, so its code is not inside the project: the
repo root comes from FACTORY_ROOT (used by the tests), else CLAUDE_PROJECT_DIR, else the git
repository the command runs in. Runtime state is under <root>/factory/, which git ignores."""

import json
import os
import subprocess
from pathlib import Path

from .errors import FactoryError


def _git_toplevel(cwd):
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(cwd), capture_output=True, text=True)
    return Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None


def _outside_workspace(root):
    if root.name == "workspace" and root.parent.name == "factory":
        return root.parent.parent
    return root


def _derive_root():
    override = os.environ.get("FACTORY_ROOT")
    if override:
        return Path(override).resolve()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return _outside_workspace(Path(project_dir).resolve())
    toplevel = _git_toplevel(Path.cwd())
    if toplevel is None:
        raise FactoryError("not inside a git repository; run the factory from the project repo")
    return _outside_workspace(toplevel.resolve())


class Paths:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else _derive_root()
        self.skill = Path(__file__).resolve().parents[2]
        self.plugin = self.skill.parents[1]
        self.factory = self.root / "factory"
        self.project = self.factory / "project.md"
        self.board = self.factory / "board.json"
        self.backlog = self.factory / "backlog.md"
        self.backlog_lock = self.factory / ".backlog.lock"
        self.jobs = self.factory / "jobs"
        self.inbox = self.factory / "inbox"
        self.inbox_done = self.inbox / "done"
        self.workspace = self.factory / "workspace"
        self.workspace_state = self.factory / "workspace.json"
        self.logs = self.factory / "logs"
        self.stop_flag = self.factory / "STOP"

    def job(self, job_id):
        return self.jobs / job_id

    def round_dir(self, job_id, n):
        return self.job(job_id) / f"round-{n}"

    def relative(self, path):
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(path)


def branch_name(paths, job_id):
    """The job's branch as recorded in job.json when it was queued; factory/<id> for jobs queued
    before branch names became configurable."""
    try:
        meta = json.loads((paths.job(job_id) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    return meta.get("branch") or f"factory/{job_id}"


def job_for_branch(paths, branch):
    if not branch:
        return None
    if paths.jobs.exists():
        for meta_file in paths.jobs.glob("*/job.json"):
            if branch_name(paths, meta_file.parent.name) == branch:
                return meta_file.parent.name
    return branch.split("/", 1)[1] if branch.startswith("factory/") else None
