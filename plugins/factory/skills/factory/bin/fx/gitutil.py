"""Thin wrappers over the git CLI. Every call is argument-list based, never a shell string."""

import subprocess
from pathlib import Path

from .errors import FactoryError


def run(args, cwd, check=True, input_text=None, timeout=600):
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise FactoryError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def out(args, cwd, check=True):
    return run(args, cwd, check=check).stdout.strip()


def ok(args, cwd):
    return run(args, cwd, check=False).returncode == 0


def rev(ref, cwd):
    result = run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def blob(ref, path, cwd):
    result = run(["rev-parse", "--verify", "--quiet", f"{ref}:{path}"], cwd, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def hash_file(path, cwd):
    if not Path(cwd, path).exists():
        return None
    return out(["hash-object", "--", path], cwd)


def is_worktree(path):
    return Path(path, ".git").exists()


def current_branch(cwd):
    return out(["branch", "--show-current"], cwd) or None


def dirty_files(cwd):
    lines = run(["status", "--porcelain", "--untracked-files=all"], cwd).stdout.splitlines()
    return [line[3:] for line in lines if line.strip()]


def unmerged_files(cwd):
    return [f for f in out(["diff", "--name-only", "--diff-filter=U"], cwd).splitlines() if f]


def git_dir(cwd):
    return Path(cwd, out(["rev-parse", "--git-dir"], cwd)).resolve()


def rebase_in_progress(cwd):
    gd = git_dir(cwd)
    return (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()


def rebasing_branch(cwd):
    gd = git_dir(cwd)
    for d in ("rebase-merge", "rebase-apply"):
        head_name = gd / d / "head-name"
        if head_name.exists():
            return head_name.read_text().strip().replace("refs/heads/", "", 1)
    return None


def changed_files(a, b, cwd):
    return [f for f in out(["diff", "--name-only", a, b], cwd).splitlines() if f]


def merge_base(a, b, cwd):
    return out(["merge-base", a, b], cwd, check=False) or None


def is_ancestor(a, b, cwd):
    return ok(["merge-base", "--is-ancestor", a, b], cwd)


def diff_size(a, b, cwd):
    total = 0
    for line in out(["diff", "--numstat", a, b], cwd).splitlines():
        added, removed, _ = (line.split("\t") + ["", ""])[:3]
        if added.isdigit():
            total += int(added)
        if removed.isdigit():
            total += int(removed)
    return total


def remote_branch_exists(remote, branch, cwd):
    if not remote:
        return False
    result = run(["ls-remote", "--exit-code", "--heads", remote, branch], cwd, check=False, timeout=120)
    return result.returncode == 0


def worktrees_on_branch(branch, cwd):
    found, current = [], None
    for line in out(["worktree", "list", "--porcelain"], cwd).splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and current:
            found.append(current)
    return found
