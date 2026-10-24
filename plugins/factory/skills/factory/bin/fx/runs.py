"""Agent runs: one record per agent the orchestrator starts, in factory/runs/<run-id>.json, so the
factory knows which work is reserved, running, done or aborted — across restarts.

    reserved  `brief` wrote the prompt; the agent may not have started. Expires after RESERVE_TTL.
    running   the plugin's PostToolUse hook on the Agent call attached the agentId Claude Code
              returned. Kept alive by the guard on each of the agent's calls; lost after run_ttl.
    done      SubagentStop for that agent_id, or `run end`.
    aborted   `run abort`, a cancel, or expiry. An aborted run's agent may write nothing.

A brief is refused while a live run holds the same work, so the same builder attempt or reviewer is
never started twice. The guard lets an agent write only through a live running run whose agent_id
matches the hook's agent_id, in that run's job — the binding comes from the orchestrator's brief,
not from the path the agent picks. If Claude Code's ids don't match, nothing writes and binding.json
says why; `next` then stops the run."""

import re
import secrets
from datetime import datetime

from . import jobs, queue
from .errors import FactoryError

RESERVE_TTL_MINUTES = 3
PRUNE_DAYS = 7
MARKER = "Factory run:"
_MARKER = re.compile(rf"^{re.escape(MARKER)}\s*(\S+)\s*$", re.M)


def _dir(paths):
    return paths.factory / "runs"


def _file(paths, run_id):
    return _dir(paths) / f"{run_id}.json"


def _minutes_since(stamp):
    return (datetime.fromisoformat(jobs.now()) - datetime.fromisoformat(stamp)).total_seconds() / 60


def run_ttl(cfg):
    """A running agent may be silent for as long as its longest command, plus a margin."""
    return cfg.suite_timeout / 60 + 15


def live(run, cfg):
    if run["status"] == "reserved":
        return _minutes_since(run["created_at"]) < RESERVE_TTL_MINUTES
    if run["status"] == "running":
        return _minutes_since(run["seen_at"]) < run_ttl(cfg)
    return False


def all_runs(paths):
    if not _dir(paths).exists():
        return []
    return [r for r in (jobs.read_json(f) for f in sorted(_dir(paths).glob("*.json"))) if r]


def for_job(paths, cfg, job_id, live_only=True):
    return [r for r in all_runs(paths) if r["job"] == job_id and (not live_only or live(r, cfg))]


def _save(paths, run):
    jobs.write_json(_file(paths, run["run"]), run)
    return run


def reserve(paths, cfg, job_id, role, round_number, attempt, head):
    """Reserves one agent's work. Refuses while a live run holds the same role in the same round
    and attempt, or, for a builder, while any builder of the job is still live."""
    with queue.locked(paths):
        for r in for_job(paths, cfg, job_id, live_only=False):
            if r["status"] in ("reserved", "running") and not live(r, cfg):
                _save(paths, {**r, "status": "aborted", "ended_at": jobs.now(),
                              "why": "expired before it started" if r["status"] == "reserved" else "went silent"})
        clash = [r for r in for_job(paths, cfg, job_id) if r["role"] == role
                 and (role == "builder" or (r["round"], r["attempt"]) == (round_number, attempt))]
        if clash:
            raise FactoryError(f"{role} for {job_id} is already {clash[0]['status']} as run {clash[0]['run']}; "
                               "wait for it, or `run abort` it if it is gone", run=clash[0])
        run = {"run": f"{job_id}.{role}.r{round_number}a{attempt or 0}.{secrets.token_hex(3)}", "job": job_id,
               "role": role, "round": round_number, "attempt": attempt, "head": head, "status": "reserved",
               "agent_id": None, "created_at": jobs.now()}
        _dir(paths).mkdir(parents=True, exist_ok=True)
        return _save(paths, run)


def run_id_in(prompt):
    m = _MARKER.search(prompt or "")
    return m.group(1) if m else None


def attach(paths, run_id, agent_id):
    """The Agent call returned: its agentId now owns the run."""
    with queue.locked(paths):
        run = jobs.read_json(_file(paths, run_id))
        if not run:
            raise FactoryError(f"no run {run_id}")
        if run["status"] != "reserved":
            raise FactoryError(f"run {run_id} is {run['status']}; it can't be attached again")
        return _save(paths, {**run, "status": "running", "agent_id": agent_id, "started_at": jobs.now(),
                             "seen_at": jobs.now()})


def for_agent(paths, agent_id):
    return next((r for r in all_runs(paths) if agent_id and r.get("agent_id") == agent_id), None)


def touch(paths, run):
    with queue.locked(paths):
        current = jobs.read_json(_file(paths, run["run"]))
        if current and current["status"] == "running":
            _save(paths, {**current, "seen_at": jobs.now()})


def end(paths, run_id=None, agent_id=None, status="done", why=None):
    with queue.locked(paths):
        run = jobs.read_json(_file(paths, run_id)) if run_id else for_agent(paths, agent_id)
        if not run:
            return None
        if run["status"] in ("reserved", "running"):
            run = _save(paths, {**run, "status": status, "ended_at": jobs.now(), "why": why})
        return run


def abort_job(paths, job_id, why):
    """Every unfinished run of a job, once it is cancelled or finished."""
    ended = []
    for r in all_runs(paths):
        if r["job"] == job_id and r["status"] in ("reserved", "running"):
            ended.append(end(paths, r["run"], status="aborted", why=why)["run"])
    return ended


def prune(paths):
    """Removes the records of runs that ended more than PRUNE_DAYS ago. Every guard call reads all
    records, and they otherwise pile up for good; an ended run's agent is long gone by then, while a
    fresh one is kept so its agent still reads as aborted rather than as unbound."""
    removed = []
    for r in all_runs(paths):
        ended = r.get("ended_at")
        if r["status"] not in ("reserved", "running") and ended and _minutes_since(ended) > PRUNE_DAYS * 24 * 60:
            _file(paths, r["run"]).unlink(missing_ok=True)
            removed.append(r["run"])
    return removed


def record_binding_failure(paths, why):
    jobs.write_json(paths.factory / "binding.json", {"failed": why, "at": jobs.now()})


def binding_failure(paths):
    return (jobs.read_json(paths.factory / "binding.json") or {}).get("failed")
