"""`board`: rebuilds factory/board.json from every job folder, backlog.md and git. It holds no
state of its own, so it is safe to run at any time; the dashboard reads it."""

from . import jobs, queue, state as state_mod
from .errors import FactoryError

STAGE_ORDER = ["queued", "starting", "testing", "building", "reviewing", "approving", "rebasing",
               "merging", "needs-human", "pr-open", "merged", "done", "cancelled", "unknown"]


def build(paths, cfg, write=True):
    ids = [e["id"] for e in queue.entries(paths)]
    if paths.jobs.exists():
        ids += sorted(d.name for d in paths.jobs.iterdir() if d.is_dir() and d.name not in ids and jobs.JOB_ID.match(d.name))
    cards = []
    for job_id in ids:
        try:
            s = state_mod.state(paths, cfg, job_id)
        except FactoryError as e:
            s = {"id": job_id, "stage": "unknown", "reason": str(e), "next": {"action": "none"}, "verdicts": {}}
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
        "jobs": cards,
    }
    if not write:
        return board
    jobs.write_json(paths.board, board)
    return {"jobs": len(cards), "file": paths.relative(paths.board)}
