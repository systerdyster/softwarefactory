"""`workspace create|switch|check|setup|run|detach`: the one persistent worktree, factory/workspace/.

It is detached at the target branch between jobs and on factory/<id> during one. It only changes
branch from a clean state, and reruns workspace_setup when a listed lockfile differs."""

import os
import shlex
import subprocess

from . import gitutil, jobs
from .errors import FactoryError
from .paths import branch_name

TAIL_LINES = 80


def _require(paths):
    if not gitutil.is_worktree(paths.workspace):
        raise FactoryError("factory/workspace does not exist; run `workspace create` (part of /factory init)")


def fetch(paths, cfg):
    if cfg.remote:
        gitutil.run(["fetch", "--prune", cfg.remote], paths.root, timeout=300)


def status(paths):
    _require(paths)
    ws = paths.workspace
    return {
        "branch": gitutil.current_branch(ws),
        "head": gitutil.rev("HEAD", ws),
        "dirty": gitutil.dirty_files(ws),
        "rebase_in_progress": gitutil.rebase_in_progress(ws),
        "unmerged": gitutil.unmerged_files(ws),
    }


def check(paths):
    s = status(paths)
    s["clean"] = not s["dirty"] and not s["rebase_in_progress"]
    return s


def _lock_hashes(paths, cfg):
    return {f: gitutil.hash_file(f, paths.workspace) for f in cfg.lockfiles}


def _shell(command, paths, timeout, log_name):
    paths.logs.mkdir(parents=True, exist_ok=True)
    log = paths.logs / f"{log_name}-{jobs.now().replace(':', '')}.log"
    env = dict(os.environ, FACTORY_REPO_ROOT=str(paths.root), FACTORY_WORKSPACE=str(paths.workspace), CI="true")
    try:
        result = subprocess.run(command, shell=True, cwd=str(paths.workspace), env=env,
                                capture_output=True, text=True, timeout=timeout)
        output, code = result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired as e:
        partial = [p.decode(errors="replace") if isinstance(p, bytes) else (p or "") for p in (e.stdout, e.stderr)]
        output = "".join(partial) + f"\n[factory] timed out after {timeout}s\n"
        code = 124
    log.write_text(f"$ {command}\n{output}\n[exit {code}]\n", encoding="utf-8")
    return {"command": command, "exit_code": code, "log": paths.relative(log),
            "tail": "\n".join(output.splitlines()[-TAIL_LINES:])}


def setup(paths, cfg):
    _require(paths)
    steps = []
    for command in cfg.setup_commands:
        step = _shell(command, paths, cfg.suite_timeout, "setup")
        steps.append(step)
        if step["exit_code"] != 0:
            raise FactoryError(f"workspace_setup failed: {command}", steps=steps)
    state = jobs.read_json(paths.workspace_state) or {}
    state["lockfiles"] = _lock_hashes(paths, cfg)
    state["setup_at"] = jobs.now()
    jobs.write_json(paths.workspace_state, state)
    return {"setup": [s["command"] for s in steps]}


def create(paths, cfg):
    if gitutil.is_worktree(paths.workspace):
        return {"created": False, "workspace": paths.relative(paths.workspace), **check(paths)}
    if paths.workspace.exists() and any(paths.workspace.iterdir()):
        raise FactoryError("factory/workspace exists but is not a git worktree; move it aside first")
    fetch(paths, cfg)
    if not gitutil.rev(cfg.base_ref, paths.root):
        raise FactoryError(f"target {cfg.base_ref} does not exist")
    gitutil.run(["worktree", "add", "--detach", str(paths.workspace), cfg.base_ref], paths.root)
    result = setup(paths, cfg)
    return {"created": True, "workspace": paths.relative(paths.workspace), **result}


def _require_clean(paths, action):
    s = check(paths)
    if not s["clean"]:
        raise FactoryError(f"workspace is not clean; refusing to {action}. Commit or discard first.",
                           dirty=s["dirty"], rebase_in_progress=s["rebase_in_progress"])
    return s


def _after_switch(paths, cfg):
    state = jobs.read_json(paths.workspace_state) or {}
    if state.get("lockfiles") != _lock_hashes(paths, cfg):
        setup(paths, cfg)
        return True
    return False


def switch(paths, cfg, job_id):
    _require(paths)
    branch = branch_name(paths, job_id)
    s = check(paths)
    if s["branch"] == branch and s["clean"]:
        return {"branch": branch, "created": False, "setup_rerun": _after_switch(paths, cfg)}
    _require_clean(paths, f"switch to {branch}")
    created = False
    if gitutil.rev(branch, paths.root):
        gitutil.run(["switch", branch], paths.workspace)
    else:
        fetch(paths, cfg)
        gitutil.run(["switch", "-c", branch, cfg.base_ref], paths.workspace)
        if cfg.remote:
            gitutil.run(["branch", "--unset-upstream"], paths.workspace, check=False)
        created = True
    return {"branch": branch, "created": created, "head": gitutil.rev("HEAD", paths.workspace),
            "setup_rerun": _after_switch(paths, cfg)}


def detach(paths, cfg, discard=False):
    _require(paths)
    if discard:
        if gitutil.rebase_in_progress(paths.workspace):
            gitutil.run(["rebase", "--abort"], paths.workspace)
        gitutil.run(["reset", "--hard", "HEAD"], paths.workspace)
        gitutil.run(["clean", "-fd"], paths.workspace)
    _require_clean(paths, "return to the target branch")
    fetch(paths, cfg)
    gitutil.run(["switch", "--detach", cfg.base_ref], paths.workspace)
    return {"branch": None, "head": gitutil.rev("HEAD", paths.workspace), "setup_rerun": _after_switch(paths, cfg)}


def run(paths, cfg, name, args):
    _require(paths)
    template = cfg.command(name)
    if not template:
        raise FactoryError(f"project.md defines no [commands] {name}; known: {cfg.command_names()}")
    quoted = " ".join(shlex.quote(a) for a in args)
    if "{target}" in template:
        command = template.replace("{target}", quoted)
    else:
        command = f"{template} {quoted}".strip()
    return _shell(command, paths, cfg.suite_timeout, f"run-{name}")


def suite(paths, cfg):
    _require(paths)
    command = cfg.command("suite")
    if not command:
        raise FactoryError("project.md defines no [commands] suite")
    result = _shell(command, paths, cfg.suite_timeout, "suite")
    result["green"] = result["exit_code"] == 0
    result["head"] = gitutil.rev("HEAD", paths.workspace)
    return result
