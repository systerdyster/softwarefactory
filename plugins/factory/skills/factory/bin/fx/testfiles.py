"""`red-check`: proof that the builder's acceptance tests test something.

The builder writes the plan's tests first and commits them, on their own or with compiling stubs, as
the first commit of the job branch, and lists them in tests.md (`| file |` rows). red-check runs those
files on that first commit, before any real change, and requires them to fail. A test that passes
there proves nothing about the change. The result is red.json in the job folder."""

import re

from . import gitutil, jobs, workspace
from .errors import FactoryError
from .paths import branch_name

_ROW = re.compile(r"^\|\s*`?([^`|]+?)`?\s*\|(?:[^|]*\|)?\s*$")


def parse(text):
    rows = []
    for line in (text or "").splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        path = m.group(1).strip()
        if path.lower() in ("file", "path") or set(path) <= set("-: "):
            continue
        rows.append(path)
    return rows


def listed(paths, job_id):
    return parse(jobs.read(paths.job(job_id) / "tests.md"))


def _record(paths, job_id, **data):
    record = {**data, "at": jobs.now()}
    jobs.write_json(paths.job(job_id) / "red.json", record)
    return record


def red_check(paths, cfg, job_id):
    branch = branch_name(paths, job_id)
    head = gitutil.rev(branch, paths.root)
    if not head:
        raise FactoryError(f"branch {branch} does not exist")
    text = jobs.read(paths.job(job_id) / "tests.md")
    if text is None:
        return _record(paths, job_id, head=head, ok=False, reason="tests.md is missing; list the tests you wrote first")
    files = parse(text)
    if not files:
        exemptions = jobs.section(text, "Exemptions")
        exempt = bool(exemptions) and not exemptions.lower().startswith("none")
        return _record(paths, job_id, head=head, ok=exempt, skipped=True,
                       reason=None if exempt else "tests.md lists no test files and no exemptions")
    base = gitutil.merge_base(cfg.base_ref, head, paths.root)
    commits = gitutil.out(["rev-list", "--reverse", f"{base}..{head}"], paths.root).split()
    if not commits:
        return _record(paths, job_id, head=head, ok=False, reason="the branch has no commits")
    first = commits[0]
    touched = gitutil.out(["diff-tree", "--no-commit-id", "--name-only", "-r", first], paths.root).splitlines()
    missing = [f for f in files if f not in touched]
    if missing:
        return _record(paths, job_id, head=head, commit=first, ok=False,
                       reason="the first commit on the branch must hold the tests; not in it: " + ", ".join(missing))
    ws = workspace.check(paths)
    if ws["branch"] != branch or ws["dirty"]:
        raise FactoryError(f"the workspace must be clean and on {branch} for red-check")
    gitutil.run(["switch", "--detach", first], paths.workspace)
    try:
        if cfg.command("test"):
            result = workspace.run(paths, cfg, "test", files)
            red, log = result["exit_code"] != 0, result["log"]
        else:
            result = workspace.suite(paths, cfg)
            red, log = not result["green"], result["log"]
    finally:
        gitutil.run(["switch", branch], paths.workspace)
    return _record(paths, job_id, head=head, commit=first, ok=red, log=log,
                   reason=None if red else "the tests pass before the change, so they prove nothing about it: "
                   + ", ".join(files))
