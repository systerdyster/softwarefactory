"""Lifecycle events: what a job has reached that the project may want to act on — claim the case, move
it in a tracker, post the test note — and the log that keeps each from running twice, also after a
restart. The factory decides when an event happens; project.md's [events] decides what it means, as a
shell command the CLI runs or a skill the session runs with its own tools.

`claimed`, `completed` and `cancelled` happen once. `started`, `waiting` and `handed-off` happen each
time the job enters that phase: a job sent back from waiting is started again."""

import os
import subprocess

from . import jobs
from .errors import FactoryError

PHASE_OF_STAGE = {"building": "started", "reviewing": "started", "rebasing": "started", "merging": "started",
                  "needs-human": "waiting", "pr-open": "handed-off"}
PHASES = ("started", "waiting", "handed-off")
COMMAND_TIMEOUT = 300


def logged(paths, job_id):
    text = jobs.read(paths.job(job_id) / "events.md") or ""
    return [line.split()[2] for line in text.splitlines() if line.startswith("- ") and len(line.split()) >= 3]


def log(paths, job_id, event, detail=""):
    path = paths.job(job_id) / "events.md"
    text = jobs.read(path) or f"# Events — {job_id}\n\n"
    jobs.write_atomic(path, text + f"- {jobs.now()} {event} {detail}".rstrip() + "\n")
    return {"logged": event}


def due(stage, done):
    """The events a job at `stage` has reached and the log doesn't hold yet, in the order they happened.
    A local merge hands the job off and completes it in one step."""
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


def todo(cfg, f, stage):
    """The handlers to run now, in order. An event the project has no handler for is skipped."""
    result = []
    for event in due(stage, f["events_log"]):
        handler = cfg.event_handler(event)
        if not handler:
            continue
        facts = context(f, event)
        item = {"event": event, **handler, "context": facts}
        if handler["kind"] == "skill":
            item["skill_args"] = " ".join([handler["args"]] + [f"{k}={v}" for k, v in facts.items()]).strip()
        result.append(item)
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
        raise FactoryError(f"[events] {event} ran longer than {COMMAND_TIMEOUT} s; not logged, so it is due again")
    output = (done.stdout + done.stderr).strip()[-2000:]
    if done.returncode != 0:
        raise FactoryError(f"[events] {event} exited {done.returncode}; not logged, so it is due again", output=output)
    log(paths, job_id, event)
    return {"ran": event, "output": output}
