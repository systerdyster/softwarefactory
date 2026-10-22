"""`guard <role>`: the per-role limits behind the plugin's PreToolUse hook (hook.py), which maps the
calling agent to its role. Agent frontmatter can grant or withhold a whole tool, but not a single
Bash command or a single path, so this enforces the per-role limits mechanically. Exit 0 allows the
call; exit 2 blocks it with the reason on stderr, which the agent sees.

Roles: builder, and review-<name> for reviewers (review-codex for the codex fallback). Reviewers may
run the project commands project.md lists under [reviewers] commands, e.g. the browser tests that
render components; nothing else that executes.

Every call that touches a job names it: its worktree (factory/workspaces/<job>), its job folder, or
`ws run <job>`. A call that writes or executes — a file write, a git write, `ws run` — is allowed only
for a live running agent run (runs.py) whose agent_id is the hook's agent_id, in that run's job and
role. The run came from the orchestrator's brief, so the binding is the orchestrator's assignment,
not the path the agent picks; a done, aborted or expired run's agent writes nothing. Reads need no
run."""

import json
import re
import shlex
import sys
from pathlib import Path

from . import config, jobs, runs
from .errors import FactoryError
from .paths import Paths

READ_GIT = {"status", "diff", "log", "show", "merge-base", "rev-parse", "ls-files", "blame", "grep", "cat-file"}
WRITE_GIT = {"add", "rm", "mv", "commit", "restore"}
BANNED_FLAGS = {"--output", "--no-verify", "--amend", "-w", "--ext-diff", "--textconv"}
METACHARS = re.compile(r"[;&|`<>\n]|\$\(")
FACTORY_CLI = "factory-cli"
READ_FACTORY = {"state", "board"}


class Blocked(Exception):
    pass


def _inside(path, folder):
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _resolve(paths, raw):
    p = Path(raw)
    return (p if p.is_absolute() else paths.root / p).resolve()


def job_of(paths, target):
    """The job a path belongs to: inside its worktree or its job folder; None otherwise."""
    for base in (paths.workspaces.resolve(), paths.jobs.resolve()):
        if _inside(target, base) and target != base:
            return target.relative_to(base).parts[0]
    return None


def _usage(role, review_commands):
    lines = ["Allowed shell commands for this role (one plain command per call, no ; && | > or $()):",
             "  git -C factory/workspaces/<job> " + "|".join(sorted(READ_GIT)) + " ..."]
    if role == "builder":
        lines.append("  git -C factory/workspaces/<job> " + "|".join(sorted(WRITE_GIT)) + " ...")
        lines.append(f"  {FACTORY_CLI} ws run <job> <command-name> [args]   (names are [commands] in project.md)")
    elif review_commands:
        lines.append(f"  {FACTORY_CLI} ws run <job> {'|'.join(review_commands)} [args]   ([reviewers] commands)")
    lines.append(f"  {FACTORY_CLI} state <job>")
    return "\n".join(lines)


def check_bash(paths, role, command, review_commands=()):
    """Returns (job, writes): the job the command touches, or None, and whether it writes or runs."""
    if METACHARS.search(command):
        raise Blocked("shell operators are not allowed.\n" + _usage(role, review_commands))
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise Blocked(f"could not parse the command: {e}")
    if not argv:
        raise Blocked("empty command")
    if argv[0] == "git":
        return _check_git(paths, role, argv, review_commands)
    if argv[0] == FACTORY_CLI and len(argv) >= 2:
        sub = argv[1]
        if sub in READ_FACTORY:
            return None, False
        if sub == "ws" and len(argv) >= 5 and argv[2] == "run":
            if role == "builder" or argv[4] in review_commands:
                return argv[3], True
        raise Blocked(f"`{' '.join(argv[:5])}` is not available to the {role}.\n" + _usage(role, review_commands))
    if argv[0] in ("ls", "pwd", "wc"):
        return None, False
    raise Blocked(f"`{argv[0]}` is not available to the {role}.\n" + _usage(role, review_commands))


def _check_git(paths, role, argv, review_commands):
    target = _resolve(paths, argv[2]) if len(argv) >= 4 and argv[1] == "-C" else None
    job = job_of(paths, target) if target else None
    if not job or target != paths.workspace(job).resolve():
        raise Blocked("git must run as `git -C factory/workspaces/<job> <command>`.\n" + _usage(role, review_commands))
    sub, rest = argv[3], argv[4:]
    banned = [a for a in rest if a.split("=", 1)[0] in BANNED_FLAGS]
    if banned:
        raise Blocked(f"git flag {banned[0]} is not allowed")
    if sub in READ_GIT:
        return job, False
    if sub in WRITE_GIT and role == "builder":
        return job, True
    raise Blocked(f"`git {sub}` is not available to the {role}. Only the orchestrator switches, rebases or pushes.\n"
                  + _usage(role, review_commands))


def check_write(paths, role, raw_path):
    """Returns the job the write lands in; refuses anything outside the role's own files."""
    if not raw_path:
        raise Blocked("no file path given")
    target = _resolve(paths, raw_path)
    if _inside(target, paths.plugin.resolve()):
        raise Blocked("the factory's own files are not changed by a factory job")
    job = job_of(paths, target)
    if not job:
        raise Blocked(f"the {role} writes only inside its job's workspace and job folder")
    jd = paths.job(job).resolve()
    if role.startswith("review-"):
        name = role[len("review-"):]
        if _inside(target, jd) and re.fullmatch(rf"round-\d+/review-{re.escape(name)}\.md", str(target.relative_to(jd))):
            return job
        raise Blocked(f"a reviewer writes only its own review file: factory/jobs/<job>/round-N/review-{name}.md")
    ws = paths.workspace(job).resolve()
    if _inside(target, ws) and not _inside(target, ws / ".git"):
        return job
    if target in (jd / "build.md", jd / "tests.md"):
        return job
    raise Blocked(f"the {role} may write only inside factory/workspaces/<job>/ and build.md and tests.md in its job folder")


def authorize(paths, cfg, agent_id, role, job):
    """Allows a write only through the agent's own live running run, in that run's job and role."""
    if not agent_id:
        raise Blocked("the hook gave no agent_id, so the factory can't tell which run this agent is; it writes "
                      "nothing. A Claude Code that sends agent_id to PreToolUse is required.")
    run = runs.for_agent(paths, agent_id)
    if not run:
        why = (f"agent_id {agent_id} matches no factory run. The run is attached from the agentId the Agent "
               "call returns; if Claude Code's two ids differ, agents can't be bound to their jobs and the "
               "factory stops building in parallel.")
        runs.record_binding_failure(paths, why)
        raise Blocked(why)
    if run["status"] != "running" or not runs.live(run, cfg):
        raise Blocked(f"run {run['run']} is {run['status'] if run['status'] != 'running' else 'expired'}; "
                      "this agent may no longer write")
    if run["job"] != job:
        raise Blocked(f"this agent's run is for {run['job']}; {job} is another job's")
    if run["role"] != role:
        raise Blocked(f"this agent's run is a {run['role']}, not a {role}")
    runs.touch(paths, run)


def _review_commands(paths, role):
    if not role.startswith("review-"):
        return []
    try:
        return config.load(paths).reviewer_commands
    except FactoryError:
        return []


def run(role, stream=None, paths=None):
    paths = paths or Paths()
    raw = (stream or sys.stdin).read()
    try:
        event = json.loads(raw or "{}")
    except json.JSONDecodeError:
        print("factory guard: unreadable hook input", file=sys.stderr)
        return 2
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    try:
        job, writes = None, False
        if tool == "Bash":
            job, writes = check_bash(paths, role, tool_input.get("command", ""), _review_commands(paths, role))
        elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            job, writes = check_write(paths, role, tool_input.get("file_path") or tool_input.get("notebook_path")), True
        elif tool in ("Agent", "Task"):
            raise Blocked("factory agents do not spawn agents")
        if job:
            jobs.check_id(job)
        if writes:
            authorize(paths, config.load(paths), event.get("agent_id"), role, job)
    except Blocked as e:
        print(f"factory guard ({role}): {e}", file=sys.stderr)
        return 2
    except FactoryError as e:
        print(f"factory guard ({role}): {e}", file=sys.stderr)
        return 2
    return 0
