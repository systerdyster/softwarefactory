"""`queue add|take|mark|list|next-id`: backlog.md under an exclusive file lock, so any number of
intake sessions and the orchestrator never write it at the same time.

backlog.md lines: `- [ ] <id> — <title>`. Marks: [ ] queued, [~] taken, [x] done, [-] cancelled."""

import contextlib
import fcntl
import re
import subprocess
import time
import unicodedata

from . import jobs
from .errors import FactoryError
from .paths import branch_name

MARKS = {"queued": " ", "taken": "~", "done": "x", "cancelled": "-"}
_LINE = re.compile(r"^- \[([ ~x-])\] (\S+)(?: — (.*))?$")
_HEADER = "# Backlog\n\nApproved jobs, oldest first. Written only through `factory.py queue`.\n\n"


@contextlib.contextmanager
def locked(paths, timeout=30):
    paths.factory.mkdir(parents=True, exist_ok=True)
    with open(paths.backlog_lock, "w") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise FactoryError("backlog.md is locked by another session; try again")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read(paths):
    return paths.backlog.read_text(encoding="utf-8") if paths.backlog.exists() else _HEADER


def entries(paths):
    result = []
    for line in _read(paths).splitlines():
        m = _LINE.match(line.strip())
        if m:
            mark = {v: k for k, v in MARKS.items()}[m.group(1)]
            result.append({"id": m.group(2), "status": mark, "title": m.group(3) or ""})
    return result


def entry(paths, job_id):
    return next((e for e in entries(paths) if e["id"] == job_id), None)


def _set_mark(text, job_id, mark):
    lines, found = [], False
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if m and m.group(2) == job_id:
            line = f"- [{mark}] {job_id}" + (f" — {m.group(3)}" if m.group(3) else "")
            found = True
        lines.append(line)
    if not found:
        raise FactoryError(f"{job_id} is not in backlog.md")
    return "\n".join(lines) + "\n"


def _slug(text, words=6):
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return "-".join(slug.split("-")[:words])


def branch_for(pattern, job_id, title, jira=None):
    """The job's branch from project.md's [handoff] branch pattern: {id} the job id, {key} the Jira
    key (else the id), {slug} the title in a few lower-case words."""
    branch = pattern.format(id=job_id, key=jira or job_id, slug=_slug(title, 5) or _slug(job_id))
    check = subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True, text=True)
    if check.returncode != 0:
        raise FactoryError(f"{branch!r} is not a valid branch name; check [handoff] branch in project.md")
    return branch


def add(paths, job_id, title, jira=None, developer=None, branch=None, work_item=None):
    jobs.check_id(job_id)
    job_dir = paths.job(job_id)
    for name in ("spec.md", "plan.md"):
        if not (job_dir / name).exists():
            raise FactoryError(f"{paths.relative(job_dir / name)} is missing; intake writes it before queueing")
    title = " ".join((title or "").split())
    if not title:
        raise FactoryError("a job needs a title")
    with locked(paths):
        if entry(paths, job_id):
            raise FactoryError(f"{job_id} is already in backlog.md")
        branch = branch or f"factory/{job_id}"
        clash = [e["id"] for e in entries(paths) if e["status"] in ("queued", "taken")
                 and branch_name(paths, e["id"]) == branch]
        if clash:
            raise FactoryError(f"branch {branch} is already used by {clash[0]}")
        meta = {"id": job_id, "title": title, "jira": jira, "developer": developer, "branch": branch,
                "work_item": work_item, "queued_at": jobs.now()}
        jobs.write_json(job_dir / "job.json", meta)
        text = _read(paths).rstrip("\n") + "\n"
        if text.strip() == _HEADER.strip():
            text = _HEADER
        jobs.write_atomic(paths.backlog, text + f"- [ ] {job_id} — {title}\n")
    return meta


def take(paths):
    with locked(paths):
        for e in entries(paths):
            if e["status"] == "queued":
                jobs.write_atomic(paths.backlog, _set_mark(_read(paths), e["id"], MARKS["taken"]))
                return e["id"]
    return None


def mark(paths, job_id, status):
    if status not in MARKS:
        raise FactoryError(f"mark must be one of {list(MARKS)}")
    with locked(paths):
        jobs.write_atomic(paths.backlog, _set_mark(_read(paths), job_id, MARKS[status]))
    return {"id": job_id, "status": status}


def next_id(paths, slug):
    slug = _slug(slug)
    if not slug:
        raise FactoryError("a slug needs at least one letter or digit")
    with locked(paths):
        taken = [int(d.name[:3]) for d in paths.jobs.glob("[0-9][0-9][0-9]-*")] if paths.jobs.exists() else []
        taken += [int(e["id"][:3]) for e in entries(paths) if e["id"][:3].isdigit()]
        job_id = f"{(max(taken) if taken else 0) + 1:03d}-{slug}"
        paths.job(job_id).mkdir(parents=True)
    return job_id
