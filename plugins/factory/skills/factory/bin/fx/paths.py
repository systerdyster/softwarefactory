"""Where everything lives. The repo root is derived from this file's location, or from
FACTORY_ROOT (used by the tests). Runtime state is under <root>/factory/, which is gitignored."""

import os
from pathlib import Path


def _derive_root():
    override = os.environ.get("FACTORY_ROOT")
    if override:
        return Path(override).resolve()
    root = Path(__file__).resolve().parents[5]
    if root.name == "workspace" and root.parent.name == "factory":
        return root.parent.parent
    return root


class Paths:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else _derive_root()
        self.skill = Path(__file__).resolve().parents[2]
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


def branch_name(job_id):
    return f"factory/{job_id}"
