"""A throwaway project for the tests: a bare origin, a clone with a passing suite, and a
factory/ folder with project.md. The suite is `python3 check.py`, which fails while a file named
BROKEN exists, so a test can turn it red with one file."""

import io
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx import config, queue, workspace  # noqa: E402
from fx.paths import Paths  # noqa: E402

PROJECT = """# Project

## Configuration

```ini
[factory]
target_branch = main
remote = origin
merge_mode = {merge_mode}
pr_tool = other
max_rounds = 3
max_diff = 400

[commands]
lint = test ! -e LINT_ERROR
suite = python3 check.py
test = python3 check.py {{target}}
echo = echo

[workspace]
setup = echo setup >> setup.log
lockfiles = deps.lock

[reviewers]
enabled = {enabled}

[paths]
ui = src/components/**, *.scss
performance = re:SELECT

[jira]
enabled = false
```
"""

CHECK = """import os, runpy, sys
if os.path.exists("BROKEN"):
    sys.exit(1)
for test in sys.argv[1:]:
    try:
        runpy.run_path(test)
    except Exception:
        sys.exit(1)
"""


def git(cwd, *args, check=True):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_EDITOR="true")
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout.strip()


class Sandbox:
    def __init__(self, merge_mode="pr", enabled=""):
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-test-")).resolve()
        self.origin = self.tmp / "origin.git"
        self.root = self.tmp / "repo"
        git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))
        git(self.tmp, "init", "-b", "main", str(self.root))
        for key, value in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
            git(self.root, "config", key, value)
        self.write("check.py", CHECK)
        self.write("src/app.py", "def answer():\n    return 41\n")
        self.write("deps.lock", "v1\n")
        self.write(".gitignore", "/factory/\nsetup.log\n__pycache__/\n")
        self.commit("Initial")
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-u", "origin", "main")
        self.paths = Paths(self.root)
        self.paths.factory.mkdir()
        self.paths.project.write_text(PROJECT.format(merge_mode=merge_mode, enabled=enabled), encoding="utf-8")
        self.cfg = config.load(self.paths)

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text, base=None):
        target = Path(base or self.root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def commit(self, message, cwd=None):
        cwd = cwd or self.root
        git(cwd, "add", "-A")
        git(cwd, "commit", "-m", message)
        return git(cwd, "rev-parse", "HEAD")

    @property
    def ws(self):
        """The worktree of the default job, 001-answer."""
        return self.paths.workspace("001-answer")

    def ws_of(self, job_id):
        return self.paths.workspace(job_id)

    def open_job(self, job_id="001-answer"):
        workspace.open_(self.paths, self.cfg, job_id)
        return self.paths.workspace(job_id)

    def queue_job(self, job_id="001-answer", title="Answer 42", after=None):
        jd = self.paths.job(job_id)
        self.write("spec.md", "# Spec\n\n## Summary\n\nThe answer is 42.\n", base=jd)
        self.write("plan.md", "# Plan\n\n1. Change answer().\n", base=jd)
        queue.add(self.paths, job_id, title, after=after)
        return job_id

    def push_target_change(self, rel, text, message="Upstream change"):
        other = self.tmp / f"other-{len(list(self.tmp.iterdir()))}"
        git(self.tmp, "clone", str(self.origin), str(other))
        for key, value in (("user.name", "t"), ("user.email", "t@t")):
            git(other, "config", key, value)
        self.write(rel, text, base=other)
        self.commit(message, cwd=other)
        git(other, "push", "origin", "main")
        git(self.root, "fetch", "origin")


class SandboxTest(unittest.TestCase):
    merge_mode = "pr"
    enabled = ""

    def setUp(self):
        self.sb = Sandbox(self.merge_mode, self.enabled)
        self.paths, self.cfg = self.sb.paths, self.sb.cfg

    def tearDown(self):
        self.sb.cleanup()


def agent_starts(root, brief, agent_id, agent_type="factory:factory-builder"):
    """What Claude Code does when the orchestrator's Agent call with this brief returns: the plugin's
    PostToolUse hook attaches the returned agentId to the brief's run."""
    import hook
    event = {"hook_event_name": "PostToolUse", "tool_name": "Agent",
             "tool_input": {"prompt": brief, "subagent_type": agent_type, "description": "factory"},
             "tool_response": {"status": "async_launched", "agentId": agent_id}}
    with _root(root):
        return hook.main(stream=io.StringIO(json.dumps(event)))


def agent_stops(root, agent_id, agent_type="factory:factory-builder"):
    import hook
    event = {"hook_event_name": "SubagentStop", "agent_id": agent_id, "agent_type": agent_type}
    with _root(root):
        return hook.main(stream=io.StringIO(json.dumps(event)))


class _root:
    def __init__(self, root):
        self.root, self.old = str(root), None

    def __enter__(self):
        self.old = os.environ.get("FACTORY_ROOT")
        os.environ["FACTORY_ROOT"] = self.root

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("FACTORY_ROOT", None)
        else:
            os.environ["FACTORY_ROOT"] = self.old
