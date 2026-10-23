"""`board`: rebuilds factory/board.json from every job folder, backlog.md and git. It holds no
state of its own, so it is safe to run at any time; the dashboard reads it."""

from datetime import datetime

from . import actions, jobs, queue, runs, state as state_mod, story
from .errors import FactoryError

STAGE_ORDER = ["queued", "starting", "building", "reviewing", "rebasing", "merging", "needs-human", "pr-open",
               "merged", "done", "cancelled", "unknown"]
STEPS = [("start", "Start"), ("build", "Build"), ("verify", "Verify"), ("tidy", "Tidy"), ("review", "Review"),
         ("approve", "Approve"), ("pr", "PR")]
KEYS = [k for k, _ in STEPS]
ACTION_STEP = {"start": "start", "spawn-builder": "build", "judge-objection": "build", "verify": "verify",
               "spawn-tidy": "tidy", "tidy-discard": "tidy", "schedule": "review", "spawn-reviewers": "review",
               "review-reset": "review", "spawn-wiki": "review", "rebase": "pr", "rebase-continue": "pr", "pr": "pr",
               "merge-local": "pr", "wait-pr": "pr", "finish": "pr"}


def _run_step(role):
    return "build" if role == "builder" else "tidy" if role == "tidy" else "review"


def _at(s):
    """The step a job is at, and how it stands there: active, waiting (for a human or a merge) or
    blocked (stopped for a human for any other reason)."""
    stage, nxt = s["stage"], s.get("next") or {}
    action = nxt.get("action")
    if stage in ("merged", "done"):
        return len(KEYS), "done"
    if stage in ("queued", "unknown", "cancelled"):
        return -1, stage
    if stage == "needs-human":
        if nxt.get("ready"):
            return KEYS.index("approve"), "waiting"
        reason = s.get("reason") or ""
        if "on the target branch" in reason:
            return KEYS.index("start"), "blocked"
        verdicts = (s.get("verdicts") or {}).get(str(s.get("round")), {})
        return KEYS.index("review" if any(verdicts.values()) else "build"), "blocked"
    if stage == "pr-open":
        return KEYS.index("pr"), "waiting"
    if action == "wait-agent" and s.get("runs"):
        return KEYS.index(_run_step(s["runs"][0]["role"])), "active"
    if action == "verify" and nxt.get("attempt") == "tidy":
        return KEYS.index("tidy"), "active"
    step = ACTION_STEP.get(action) or ("pr" if stage in ("rebasing", "merging") else
                                       "review" if stage == "reviewing" else "start" if stage == "starting" else "build")
    return KEYS.index(step), "active"


def pipeline(s):
    """One job's progress as the dashboard draws it: every step done, current or still to come."""
    pos, how = _at(s)
    n = s.get("round") or 0
    runs = s.get("runs") or []
    tidy = s.get("tidy")
    steps = []
    for i, (key, label) in enumerate(STEPS):
        step = {"key": key, "label": label,
                "status": "done" if i < pos else how if i == pos else "pending"}
        if key == "tidy" and i != pos and (i < pos or n > 1):
            kept = tidy and tidy.get("status") == "done" and not tidy.get("discarded")
            step["status"] = "done" if kept else "skipped"
            if tidy and tidy.get("discarded"):
                step["detail"] = f"discarded: {tidy['discarded']}"
        if i == pos:
            run = next((r for r in runs if _run_step(r["role"]) == key), None)
            if run and key != "review":
                step["since"] = run["since"]
            if key == "build":
                attempt = (s.get("next") or {}).get("attempt")
                step["detail"] = " · ".join(p for p in (f"fix round {n}" if n > 1 else "",
                                                        f"attempt {attempt}" if isinstance(attempt, int) and attempt > 1 else "") if p)
            elif key == "approve":
                step["detail"] = "waiting for you"
            elif key == "pr":
                step["detail"] = {"rebasing": "rebasing", "merging": "merging", "pr-open": "open, waiting for merge"}.get(s["stage"], "")
        if key == "review" and n:
            verdicts = (s.get("verdicts") or {}).get(str(n), {})
            running = {r["role"][len("review-"):]: r for r in runs if r["role"].startswith("review-")}
            step["reviewers"] = [{"name": name, "verdict": v,
                                  "status": v or ("running" if name in running else "waiting"),
                                  "since": running[name]["since"] if name in running else None}
                                 for name, v in sorted(verdicts.items())]
        steps.append(step)
    return steps


def orchestrator(paths):
    """Whether an orchestrator is there to run commands: its lease, and when it was last heard from.
    It refreshes the lease only between steps, so its running agents count too — each of their tool
    calls is a sign of life."""
    lease = jobs.read_json(paths.orchestrator)
    if not lease:
        return {"state": "none"}
    seen = [lease["seen_at"]] + [r["seen_at"] for r in runs.all_runs(paths) if r["status"] == "running" and r.get("seen_at")]
    last = max(seen)
    age = (datetime.fromisoformat(jobs.now()) - datetime.fromisoformat(last)).total_seconds() / 60
    return {"state": "running" if age < actions.LEASE_STALE_MINUTES else "stale", "last_seen": last}


def build(paths, cfg, write=True):
    statuses = {e["id"]: e["status"] for e in queue.entries(paths)}
    ids = list(statuses)
    if paths.jobs.exists():
        ids += sorted(d.name for d in paths.jobs.iterdir() if d.is_dir() and d.name not in ids and jobs.JOB_ID.match(d.name))
    cards = []
    for job_id in ids:
        try:
            s = state_mod.state(paths, cfg, job_id)
        except FactoryError as e:
            s = {"id": job_id, "stage": "unknown", "reason": str(e), "next": {"action": "none"}, "verdicts": {}}
        s["pipeline"] = pipeline(s)
        s["waits_for"] = queue.blocked_by(paths, job_id, statuses) if s["stage"] == "queued" else []
        s["story"] = story.story(paths, job_id, s)
        s["decision"] = story.decision(paths, job_id, s, cfg)
        s["confidence"] = story.confidence(paths, job_id)
        jd = paths.job(job_id)
        s["files"] = sorted(str(p.relative_to(jd)) for p in jd.rglob("*.md")) if jd.exists() else []
        s["queued_at"] = (jobs.read_json(jd / "job.json") or {}).get("queued_at") if jd.exists() else None
        cards.append(s)
    board = {
        "updated_at": jobs.now(),
        "stages": STAGE_ORDER,
        "merge_mode": cfg.merge_mode,
        "target": cfg.base_ref,
        "reviewers": cfg.enabled_reviewers,
        "stopping": paths.stop_flag.exists(),
        "orchestrator": orchestrator(paths),
        "jobs": cards,
    }
    if not write:
        return board
    jobs.write_json(paths.board, board)
    return {"jobs": len(cards), "file": paths.relative(paths.board)}
