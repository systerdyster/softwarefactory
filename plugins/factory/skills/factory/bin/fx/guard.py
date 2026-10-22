"""`guard <role>`: the per-role limits behind the plugin's PreToolUse hook (hook.py), which maps the
calling agent to its role. Agent frontmatter can grant or withhold a whole tool, but not a single
Bash command or a single path, so this enforces the per-role limits mechanically. Exit 0 allows the
call; exit 2 blocks it with the reason on stderr, which the agent sees.

Roles: builder, and review-<name> for reviewers (review-codex for the codex fallback). Reviewers may
run the project commands project.md lists under [reviewers] commands, e.g. the browser tests that
render components; nothing else that executes."""

import json
import re
import shlex
import sys
from pathlib import Path

from . import config, gitutil
from .errors import FactoryError
from .paths import Paths, job_for_branch

READ_GIT = {"status", "diff", "log", "show", "merge-base", "rev-parse", "ls-files", "blame", "grep", "cat-file"}
WRITE_GIT = {"add", "rm", "mv", "commit", "restore"}
BANNED_FLAGS = {"--output", "--no-verify", "--amend", "-w", "--ext-diff", "--textconv"}
METACHARS = re.compile(r"[;&|`<>\n]|\$\(")
FACTORY_CLI = "factory-cli"
READ_FACTORY = {"state", "board"}


class Blocked(Exception):
    pass


def _current_job(paths):
    if not gitutil.is_worktree(paths.workspace):
        return None
    branch = gitutil.rebasing_branch(paths.workspace) if gitutil.rebase_in_progress(paths.workspace) \
        else gitutil.current_branch(paths.workspace)
    return job_for_branch(paths, branch)


def _inside(path, folder):
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _resolve(paths, raw):
    p = Path(raw)
    return (p if p.is_absolute() else paths.root / p).resolve()


def _usage(role, review_commands):
    lines = ["Allowed shell commands for this role (one plain command per call, no ; && | > or $()):",
             "  git -C factory/workspace " + "|".join(sorted(READ_GIT)) + " ..."]
    if role == "builder":
        lines.append("  git -C factory/workspace " + "|".join(sorted(WRITE_GIT)) + " ...")
        lines.append(f"  {FACTORY_CLI} ws run <command-name> [args]   (names are [commands] in project.md)")
    elif review_commands:
        lines.append(f"  {FACTORY_CLI} ws run {'|'.join(review_commands)} [args]   ([reviewers] commands in project.md)")
    lines.append(f"  {FACTORY_CLI} state <job-id>")
    return "\n".join(lines)


def check_bash(paths, role, command, review_commands=()):
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
            return
        if sub == "ws" and len(argv) >= 4 and argv[2] == "run":
            if role == "builder" or argv[3] in review_commands:
                return
        raise Blocked(f"`{' '.join(argv[:4])}` is not available to the {role}.\n" + _usage(role, review_commands))
    if argv[0] in ("ls", "pwd", "wc"):
        return
    raise Blocked(f"`{argv[0]}` is not available to the {role}.\n" + _usage(role, review_commands))


def _check_git(paths, role, argv, review_commands):
    if len(argv) < 4 or argv[1] != "-C" or _resolve(paths, argv[2]) != paths.workspace.resolve():
        raise Blocked("git must run as `git -C factory/workspace <command>`.\n" + _usage(role, review_commands))
    sub, rest = argv[3], argv[4:]
    banned = [a for a in rest if a.split("=", 1)[0] in BANNED_FLAGS]
    if banned:
        raise Blocked(f"git flag {banned[0]} is not allowed")
    if sub in READ_GIT:
        return
    if sub in WRITE_GIT and role == "builder":
        return
    raise Blocked(f"`git {sub}` is not available to the {role}. Only the orchestrator switches, rebases or pushes.\n"
                  + _usage(role, review_commands))


def check_write(paths, role, raw_path, job_id):
    if not raw_path:
        raise Blocked("no file path given")
    target = _resolve(paths, raw_path)
    if _inside(target, paths.plugin.resolve()):
        raise Blocked("the factory's own files are not changed by a factory job")
    ws = paths.workspace.resolve()
    jd = paths.job(job_id).resolve() if job_id else None
    if role.startswith("review-"):
        name = role[len("review-"):]
        if jd and _inside(target, jd) and re.fullmatch(rf"round-\d+/review-{re.escape(name)}\.md",
                                                       str(target.relative_to(jd))):
            return
        raise Blocked(f"a reviewer writes only its own review file: factory/jobs/<id>/round-N/review-{name}.md")
    if _inside(target, ws) and not _inside(target, ws / ".git"):
        return
    if jd and target in (jd / "build.md", jd / "tests.md"):
        return
    raise Blocked(f"the {role} may write only inside factory/workspace/ and build.md and tests.md in its job folder")


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
        if tool == "Bash":
            check_bash(paths, role, tool_input.get("command", ""), _review_commands(paths, role))
        elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            check_write(paths, role, tool_input.get("file_path") or tool_input.get("notebook_path"),
                        _current_job(paths))
        elif tool in ("Agent", "Task"):
            raise Blocked("factory agents do not spawn agents")
    except Blocked as e:
        print(f"factory guard ({role}): {e}", file=sys.stderr)
        return 2
    return 0
