"""Lifecycle events: what a job has reached that the project may want to act on — claim the case, move
it in a tracker, post the test note — and the log that keeps each from running twice, also after a
restart. The factory decides when an event happens; project.md's [events] decides what it means, as a
shell command the CLI runs or a skill the session runs with its own tools.

`claimed`, `completed` and `cancelled` happen once. `started`, `waiting` and `handed-off` happen when
the job's phase differs from the last phase a handler ran for: a job sent back from waiting is started
again once its `waiting` ran. A phase with no handler, or one that failed, leaves the last phase as it
was, so the job returning to it runs nothing; a handler keeps a tracker in step, and the tracker
never left that phase either. The factory's own
steps after the developer approved — rebasing, merging, retrying a blocked merge — start nothing, so
a case in review is not moved back and forth each time the target moves.

A handler that fails is logged as failed and due again, up to MAX_FAILURES times in all, or for a phase
for the same entry into it; then it is given up, and state reports it instead of running it on every
step."""

import os
import subprocess

from . import jobs
from .errors import FactoryError

PHASE_OF_STAGE = {"building": "started", "reviewing": "started", "needs-human": "waiting", "pr-open": "handed-off"}
PHASES = ("started", "waiting", "handed-off")
COMMAND_TIMEOUT = 300
MAX_FAILURES = 3
FAILED = "failed:"


def logged(paths, job_id):
    text = jobs.read(paths.job(job_id) / "events.md") or ""
    return [line.split()[2] for line in text.splitlines() if line.startswith("- ") and len(line.split()) >= 3]


def _header(job_id):
    return f"# Events — {job_id}\n\n"


def log(paths, job_id, event, detail=""):
    path = paths.job(job_id) / "events.md"
    text = jobs.read(path) or _header(job_id)
    jobs.write_atomic(path, text + f"- {jobs.now()} {event} {detail}".rstrip() + "\n")
    return {"logged": event}


def open_log(paths, job_id):
    """Marks a job finished or cancelled by this factory as one whose last events `next` still runs; a
    job finished before events existed has no log and is left alone."""
    path = paths.job(job_id) / "events.md"
    if not path.exists():
        jobs.write_atomic(path, _header(job_id))


def log_failure(paths, job_id, event, why):
    return log(paths, job_id, FAILED + event, " ".join((why or "no reason given").split())[:300])


def _done(entries):
    return [e for e in entries if not e.startswith(FAILED)]


def failures(entries, event):
    """How often `event` failed: a once-only event over the job's whole log, a phase since the job last
    entered a phase, so another event's handler running never revives one given up."""
    count = 0
    for entry in reversed(entries):
        if entry == FAILED + event:
            count += 1
        elif event in PHASES and entry in PHASES:
            break
    return count


def due(stage, entries):
    """The events a job at `stage` has reached and the log doesn't hold as run yet, in the order they
    happened. A local merge hands the job off and completes it in one step."""
    done = _done(entries)
    events = []
    if stage != "cancelled" and "claimed" not in done:
        events.append("claimed")
    last = next((e for e in reversed(done) if e in PHASES), None)
    phase = PHASE_OF_STAGE.get(stage)
    if phase and phase != last:
        events.append(phase)
    if stage in ("merged", "done"):
        if last != "handed-off":
            events.append("handed-off")
        if "completed" not in done:
            events.append("completed")
    if stage == "cancelled" and "cancelled" not in done:
        events.append("cancelled")
    return events


def context(f, event):
    """What a handler gets to know: the job, its case and branch, the PR, and — at the first hand-off,
    when the developer left one — the test note, which a tracker comment carries."""
    meta = f["meta"]
    facts = {"job": f["id"], "case": jobs.case_key(meta), "branch": meta.get("branch"),
             "developer": meta.get("developer"), "pr": (f.get("pr") or {}).get("url")}
    if event == "handed-off" and "handed-off" not in f["events_log"] and f.get("test_note"):
        facts["test_note"] = f["test_note_file"]
    return {k: v for k, v in facts.items() if v}


def _pair(key, value):
    value = str(value)
    return f'{key}="{value}"' if any(c.isspace() for c in value) else f"{key}={value}"


def given_up(cfg, f, stage):
    """The due events whose handler failed MAX_FAILURES times: reported, no longer run."""
    return [e for e in due(stage, f["events_log"])
            if cfg.event_handler(e) and failures(f["events_log"], e) >= MAX_FAILURES]


def todo(cfg, f, stage):
    """The handlers to run now, in order. An event the project has no handler for is skipped, and so is
    one given up; a later event still runs, since each handler stands alone."""
    result = []
    for event in due(stage, f["events_log"]):
        handler = cfg.event_handler(event)
        if not handler or failures(f["events_log"], event) >= MAX_FAILURES:
            continue
        facts = context(f, event)
        item = {"event": event, **handler, "context": facts}
        if handler["kind"] == "skill":
            item["skill_args"] = " ".join([handler["args"]] + [_pair(k, v) for k, v in facts.items()]).strip()
        result.append(item)
    return result


def unfinished(paths, cfg, entries):
    """The finished and cancelled jobs with a handler still due. They have left the loop, so `next`
    lists them until each has run or been given up — also after a failure or a restart."""
    result = []
    for e in entries:
        if e["status"] not in ("done", "cancelled") or not (paths.job(e["id"]) / "events.md").exists():
            continue
        entries_logged = logged(paths, e["id"])
        if any(cfg.event_handler(event) and failures(entries_logged, event) < MAX_FAILURES
               for event in due(e["status"], entries_logged)):
            result.append(e["id"])
    return result


def run(paths, cfg, job_id, event, current):
    """Runs the command [events] names for `event`, when the job's state lists it as due, in the project
    root with the context as FACTORY_* variables, and logs it once it exits 0. A failed command is not
    logged, so it is due again."""
    item = next((t for t in current["events_todo"] if t["event"] == event), None)
    if not item:
        raise FactoryError(f"{event} is not due for {job_id}; due: {[t['event'] for t in current['events_todo']]}")
    if item["kind"] != "command":
        raise FactoryError(f"{event} runs the skill {item['skill']}; the session runs it, then `event log`")
    env = {**os.environ, "FACTORY_EVENT": event,
           **{f"FACTORY_{k.upper()}": str(v) for k, v in item["context"].items()}}
    try:
        done = subprocess.run(item["command"], shell=True, cwd=paths.root, env=env, capture_output=True,
                              text=True, timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        why = f"ran longer than {COMMAND_TIMEOUT} s"
        log_failure(paths, job_id, event, why)
        raise FactoryError(f"[events] {event} {why}; logged as failed")
    output = (done.stdout + done.stderr).strip()[-2000:]
    if done.returncode != 0:
        log_failure(paths, job_id, event, f"exited {done.returncode}: {output[-200:]}")
        raise FactoryError(f"[events] {event} exited {done.returncode}; logged as failed", output=output)
    log(paths, job_id, event)
    return {"ran": event, "output": output}
