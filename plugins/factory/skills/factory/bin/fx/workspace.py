"""`workspace open|check|remove|list`, `ws run`, `suite`: one persistent worktree per job,
factory/workspaces/<job-id>/, on the job's branch.

A job's worktree is created when the job starts and kept through building, review, fixes and waiting
for approval; it is removed when the job is finished or cancelled. Each worktree has its own
dependencies (for pnpm its own node_modules, from pnpm's shared store); workspace_setup runs when the
worktree is created and again only when a listed lockfile changes.

Every command runs with FACTORY_JOB, FACTORY_WORKSPACE, FACTORY_OUTPUT (a folder of the job's own)
and FACTORY_PORT (a free port) in its environment, so dev servers and browser tests of two jobs don't
collide. Commands listed in [factory] serial — migrations, seeding, tests that write shared data —
run one at a time across all jobs."""

import contextlib
import fcntl
import hashlib
import os
import shlex
import shutil
import socket
import subprocess

from . import gitutil, jobs
from .errors import FactoryError
from .paths import branch_name

TAIL_LINES = 80
PROBE = ".probe"


def _require(paths, job_id):
    if not gitutil.is_worktree(paths.workspace(job_id)):
        raise FactoryError(f"{job_id} has no workspace; `workspace open {job_id}` creates it")


def fetch(paths, cfg):
    if cfg.remote:
        gitutil.run(["fetch", "--prune", cfg.remote], paths.root, timeout=300)


def status(paths, job_id):
    _require(paths, job_id)
    ws = paths.workspace(job_id)
    return {
        "job": job_id,
        "branch": gitutil.current_branch(ws),
        "head": gitutil.rev("HEAD", ws),
        "dirty": gitutil.dirty_files(ws),
        "rebase_in_progress": gitutil.rebase_in_progress(ws),
        "unmerged": gitutil.unmerged_files(ws),
    }


def check(paths, job_id):
    s = status(paths, job_id)
    s["clean"] = not s["dirty"] and not s["rebase_in_progress"]
    return s


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _serial(paths, name):
    """One at a time across every job, for a command that writes shared data."""
    paths.locks.mkdir(parents=True, exist_ok=True)
    with open(paths.locks / f"{name}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _shell(command, paths, cfg, cwd, log_name, job_id, name=None):
    logs = paths.logs / (job_id or "_")
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{log_name}-{jobs.now().replace(':', '')}.log"
    out = paths.output(job_id or PROBE)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FACTORY_REPO_ROOT=str(paths.root), FACTORY_WORKSPACE=str(cwd), FACTORY_JOB=job_id or "",
               FACTORY_OUTPUT=str(out), FACTORY_PORT=str(_free_port()), CI="true")
    serial = _serial(paths, name) if name and name in cfg.serial_commands else contextlib.nullcontext()
    with serial:
        try:
            result = subprocess.run(command, shell=True, cwd=str(cwd), env=env,
                                    capture_output=True, text=True, timeout=cfg.suite_timeout)
            output, code = result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired as e:
            partial = [p.decode(errors="replace") if isinstance(p, bytes) else (p or "") for p in (e.stdout, e.stderr)]
            output = "".join(partial) + f"\n[factory] timed out after {cfg.suite_timeout}s\n"
            code = 124
    log.write_text(f"$ {command}\n{output}\n[exit {code}]\n", encoding="utf-8")
    return {"command": command, "exit_code": code, "log": paths.relative(log),
            "tail": "\n".join(output.splitlines()[-TAIL_LINES:])}


def _lock_hashes(cfg, cwd):
    return {f: gitutil.hash_file(f, cwd) for f in cfg.lockfiles}


def _setup(paths, cfg, cwd, job_id):
    steps = []
    for command in cfg.setup_commands:
        step = _shell(command, paths, cfg, cwd, "setup", job_id)
        steps.append(step)
        if step["exit_code"] != 0:
            raise FactoryError(f"workspace_setup failed: {command}", steps=steps)
    return [s["command"] for s in steps]


def setup(paths, cfg, job_id):
    _require(paths, job_id)
    ran = _setup(paths, cfg, paths.workspace(job_id), job_id)
    jobs.write_json(paths.job(job_id) / "workspace.json",
                    {"lockfiles": _lock_hashes(cfg, paths.workspace(job_id)), "setup_at": jobs.now()})
    return {"setup": ran}


def ensure_setup(paths, cfg, job_id):
    """Reruns workspace_setup only when a listed lockfile differs from the last setup."""
    state = jobs.read_json(paths.job(job_id) / "workspace.json") or {}
    if state.get("lockfiles") != _lock_hashes(cfg, paths.workspace(job_id)):
        setup(paths, cfg, job_id)
        return True
    return False


def open_(paths, cfg, job_id):
    """The job's worktree, created on its branch (from the target when the branch is new)."""
    ws = paths.workspace(job_id)
    branch = branch_name(paths, job_id)
    if gitutil.is_worktree(ws):
        return {"created": False, "workspace": paths.relative(ws), "setup_rerun": ensure_setup(paths, cfg, job_id)}
    if ws.exists() and any(ws.iterdir()):
        raise FactoryError(f"{paths.relative(ws)} exists but is not a git worktree; move it aside first")
    paths.workspaces.mkdir(parents=True, exist_ok=True)
    created_branch = not gitutil.rev(branch, paths.root)
    if created_branch:
        fetch(paths, cfg)
        if not gitutil.rev(cfg.base_ref, paths.root):
            raise FactoryError(f"target {cfg.base_ref} does not exist")
        gitutil.run(["worktree", "add", "-b", branch, str(ws), cfg.base_ref], paths.root)
        if cfg.remote:
            gitutil.run(["branch", "--unset-upstream", branch], paths.root, check=False)
    else:
        gitutil.run(["worktree", "add", str(ws), branch], paths.root)
    result = setup(paths, cfg, job_id)
    return {"created": True, "branch_created": created_branch, "workspace": paths.relative(ws), **result}


def remove(paths, cfg, job_id, discard=False):
    """Removes the job's worktree and its output folder. Refuses uncommitted work unless discard."""
    ws = paths.workspace(job_id)
    if not gitutil.is_worktree(ws):
        return {"removed": False}
    s = check(paths, job_id)
    if not s["clean"] and not discard:
        raise FactoryError(f"{paths.relative(ws)} has uncommitted work; refusing to remove it",
                           dirty=s["dirty"], rebase_in_progress=s["rebase_in_progress"])
    if s["rebase_in_progress"]:
        gitutil.run(["rebase", "--abort"], ws)
    gitutil.run(["worktree", "remove", "--force", str(ws)], paths.root)
    shutil.rmtree(paths.output(job_id), ignore_errors=True)
    return {"removed": True, "workspace": paths.relative(ws)}


def listing(paths):
    if not paths.workspaces.exists():
        return []
    return [check(paths, d.name) for d in sorted(paths.workspaces.iterdir())
            if d.name != PROBE and gitutil.is_worktree(d)]


def run(paths, cfg, job_id, name, args):
    _require(paths, job_id)
    template = cfg.command(name)
    if not template:
        raise FactoryError(f"project.md defines no [commands] {name}; known: {cfg.command_names()}")
    quoted = " ".join(shlex.quote(a) for a in args)
    if "{target}" in template:
        command = template.replace("{target}", quoted)
    else:
        command = f"{template} {quoted}".strip()
    return _shell(command, paths, cfg, paths.workspace(job_id), f"run-{name}", job_id, name)


def fingerprint(cfg):
    """Identifies what a verify ran: the command names and their exact command lines. A verify result
    counts only for the same code and the same fingerprint."""
    text = "\n".join(f"{name}={cfg.command(name)}" for name in cfg.verify_commands)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _verify(paths, cfg, cwd, job_id):
    steps, result = [], None
    for name in cfg.verify_commands:
        result = _shell(cfg.command(name), paths, cfg, cwd, f"verify-{name}", job_id, name)
        steps.append({"name": name, "exit_code": result["exit_code"], "log": result["log"]})
        if result["exit_code"] != 0:
            break
    green = all(s["exit_code"] == 0 for s in steps)
    return {"green": green, "head": gitutil.rev("HEAD", cwd), "steps": steps,
            "failed": None if green else steps[-1]["name"], "log": result["log"],
            "tail": "" if green else result["tail"], "fingerprint": fingerprint(cfg)}


def verify(paths, cfg, job_id):
    """The project's own checks — [factory] verify, by default lint, build and suite where defined — in
    the job's worktree, in order, stopping at the first red one."""
    _require(paths, job_id)
    ensure_setup(paths, cfg, job_id)
    return _verify(paths, cfg, paths.workspace(job_id), job_id)


def probe(paths, cfg):
    """For /factory init: a throwaway worktree at the target, set up and checked, then removed."""
    ws = paths.workspaces / PROBE
    if gitutil.is_worktree(ws):
        gitutil.run(["worktree", "remove", "--force", str(ws)], paths.root)
    fetch(paths, cfg)
    if not gitutil.rev(cfg.base_ref, paths.root):
        raise FactoryError(f"target {cfg.base_ref} does not exist")
    paths.workspaces.mkdir(parents=True, exist_ok=True)
    gitutil.run(["worktree", "add", "--detach", str(ws), cfg.base_ref], paths.root)
    try:
        ran = _setup(paths, cfg, ws, None)
        return {**_verify(paths, cfg, ws, None), "setup": ran}
    finally:
        gitutil.run(["worktree", "remove", "--force", str(ws)], paths.root, check=False)
