"""`state <job-id>`: where a job stands and what happens next, derived only from the job folder,
backlog.md and git. The orchestrator asks this before every action.

gather() collects facts; decide() is a pure function of those facts, so the rules are testable
without git. Network state (PR status) is read from pr.json, which `pr status` refreshes."""

import json
from pathlib import Path

from . import gitutil, jobs, queue, runs, workspace
from .config import REQUIRED_REVIEWERS
from .paths import branch_name

MAX_ATTEMPTS = 3
STUCK_REPEATS = 3
WAITING_STAGES = ("needs-human", "pr-open")
FINAL_STAGES = ("done", "cancelled")
READY = "every review passed; ready for your approval"


def _round_facts(paths, job_id, n):
    d = paths.round_dir(job_id, n)
    rulings, verifies = {}, {}
    for f in d.glob("ruling-*.md"):
        rulings[int(f.stem.rsplit("-", 1)[1])] = jobs.ruling(f.read_text(encoding="utf-8"))
    for f in d.glob("answer-*.md"):
        attempt = int(f.stem.rsplit("-", 1)[1])
        if rulings.get(attempt) == "NEEDS-HUMAN":
            rulings[attempt] = "ANSWERED"
    for f in d.glob("verify-*.json"):
        key = f.stem.split("-", 1)[1]
        verifies[int(key) if key.isdigit() else key] = jobs.read_json(f)
    return {"scheduled": jobs.scheduled(d), "reviews": jobs.reviews(d), "rulings": rulings, "verifies": verifies}


def _tidy_facts(paths, job_id, spawns):
    """The tidy pass runs once per job, in round 1, on top of the builder's green result: the head its
    brief was written for is what a discard goes back to."""
    spawn = next((s for s in spawns if s.get("agent") == "tidy"), None)
    if not spawn:
        return None
    text = jobs.read(paths.round_dir(job_id, 1) / "tidy.md") or ""
    return {"before": spawn["head"], "status": (jobs.field(text, "Status") or "").lower() or None,
            "discarded": jobs.field(text, "Discarded")}


def gather(paths, cfg, job_id):
    jd = paths.job(job_id)
    branch = branch_name(paths, job_id)
    head = gitutil.rev(branch, paths.root)
    cwd = paths.workspace(job_id)
    ws = {"exists": gitutil.is_worktree(cwd), "on_branch": False}
    if ws["exists"]:
        rebasing = gitutil.rebase_in_progress(cwd)
        ws["branch"] = gitutil.rebasing_branch(cwd) if rebasing else gitutil.current_branch(cwd)
        ws["on_branch"] = ws["branch"] == branch
        if ws["on_branch"]:
            ws["rebase"] = rebasing
            ws["dirty"] = [] if rebasing else gitutil.dirty_files(cwd)
            ws["unmerged"] = gitutil.unmerged_files(cwd) if rebasing else []
    decision_text = jobs.read(jd / "decision.md")
    human_text = jobs.read(jd / "human-approval.md")
    rounds = {n: _round_facts(paths, job_id, n) for n in jobs.rounds_on_disk(jd)}
    spawns = _spawn_log(jd)
    if 1 in rounds:
        rounds[1]["tidy"] = _tidy_facts(paths, job_id, spawns)
    entry = queue.entry(paths, job_id)
    return {
        "id": job_id,
        "meta": jobs.read_json(jd / "job.json") or {},
        "has_spec": (jd / "spec.md").exists() and (jd / "plan.md").exists(),
        "backlog": entry["status"] if entry else None,
        "branch_exists": head is not None,
        "head": head,
        "target_head": gitutil.rev(cfg.base_ref, paths.root),
        "workspace": ws,
        "baseline": jobs.read_json(jd / "baseline.json"),
        "verify_fingerprint": workspace.fingerprint(cfg),
        "build": jobs.build_sections(jobs.read(jd / "build.md")),
        "rounds": rounds,
        "reworks": jobs.reworks(jd),
        "cancelled": jobs.decision(decision_text) == "CANCELLED" if decision_text else False,
        "human_approval": {"head": jobs.field(human_text, "Head")} if human_text else None,
        "rebase": jobs.read_json(jd / "rebase.json") or {"history": []},
        "pr": jobs.read_json(jd / "pr.json"),
        "merge": jobs.read_json(jd / "merge.json"),
        "merged_locally": bool(head) and (jd / "merge.json").exists() and gitutil.rev(cfg.target_branch, paths.root) is not None
        and gitutil.is_ancestor(head, cfg.target_branch, paths.root),
        "spawns": spawns,
        "jira_log": _jira_log(jd),
        "required": REQUIRED_REVIEWERS,
        "max_rounds": cfg.max_rounds,
        "merge_mode": cfg.merge_mode,
        "jira_enabled": cfg.jira_enabled and bool((jobs.read_json(jd / "job.json") or {}).get("jira")),
        "jira_map": {s: cfg.jira_status(s) for s in ("building", "needs-human", "pr-open", "merged", "cancelled")},
    }


def _spawn_log(jd):
    text = jobs.read(jd / "spawns.jsonl") or ""
    result = []
    for line in text.splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _jira_log(jd):
    text = jobs.read(jd / "jira.md") or ""
    return [line.split()[2] for line in text.splitlines() if line.startswith("- ") and len(line.split()) >= 3]


def _equivalent_heads(f, head):
    heads = {head}
    changed = True
    while changed:
        changed = False
        for e in f["rebase"].get("history", []):
            if e.get("result") == "green" and e.get("after") in heads and e.get("before") not in heads:
                heads.add(e["before"])
                changed = True
    return heads


def _cycle_start(f):
    starts = [r["start_round"] for r in f["reworks"] if r["start_round"]]
    return max(starts) if starts else 1


def _sections(f, n):
    return [s for s in f["build"] if s["round"] == n]


def _effective_verdicts(f, up_to, start):
    result = {}
    for n in sorted(f["rounds"]):
        if start <= n <= up_to:
            for name, review in f["rounds"][n]["reviews"].items():
                if review["verdict"]:
                    result[name] = review["verdict"]
    return result


def _out(f, stage, action=None, reason=None, **extra):
    nxt = {"action": action or "none"}
    nxt.update({k: v for k, v in extra.items() if v is not None})
    result = {"id": f["id"], "title": f["meta"].get("title", ""), "jira": f["meta"].get("jira"),
              "stage": stage, "next": nxt, "reason": reason,
              "waiting": stage in WAITING_STAGES, "final": stage in FINAL_STAGES}
    return result


def _stuck(f, agent, round_number):
    recent = [s for s in f["spawns"] if s.get("agent") == agent and s.get("round") == round_number]
    tail = recent[-STUCK_REPEATS:]
    current = (f["head"], tuple(f["workspace"].get("unmerged") or []))
    return len(tail) == STUCK_REPEATS and all((s.get("head"), tuple(s.get("conflicts") or [])) == current for s in tail)


def _builder(f, stage, n, attempt, reason):
    if attempt > MAX_ATTEMPTS:
        return _out(f, "needs-human", "needs-human",
                    f"builder made {MAX_ATTEMPTS} attempts in round {n} without finishing: {reason}")
    if _stuck(f, "builder", n):
        return _out(f, "needs-human", "needs-human",
                    f"builder spawned {STUCK_REPEATS} times in round {n} without a new commit: {reason}")
    if not f["workspace"].get("on_branch"):
        return _out(f, stage, "open-workspace", reason)
    return _out(f, stage, "spawn-builder", reason, round=n, attempt=attempt)


def decide(f):
    if f["cancelled"] or f["backlog"] == "cancelled":
        if f["branch_exists"]:
            return _out(f, "cancelled", "cancel", "cancelled; branch still exists")
        return _out(f, "cancelled", reason="cancelled")
    merged = (f["pr"] or {}).get("state") == "MERGED" or (f["merge"] or {}).get("merged") or f.get("merged_locally")
    if merged or f["backlog"] == "done":
        if f["branch_exists"] or f["backlog"] != "done":
            return _out(f, "merged", "finish", "merged; clean up branches and mark done")
        return _out(f, "done", reason="merged")
    if f["backlog"] is None:
        return _out(f, "unknown", "none", "not in backlog.md")
    if not f["has_spec"]:
        return _out(f, "needs-human", "needs-human", "spec.md or plan.md is missing")
    if f["backlog"] == "queued" and not f["baseline"]:
        return _out(f, "queued", "take", "waiting in the queue")
    if not f["baseline"] or not f["branch_exists"]:
        return _out(f, "starting", "start", "switch from the target branch and prove its checks green")
    if not f["baseline"].get("green"):
        if f["baseline"].get("target") and f["baseline"].get("target") != f["target_head"]:
            return _out(f, "starting", "start", "the target branch moved since its checks were red; try again")
        return _out(f, "needs-human", "needs-human",
                    f"{f['baseline'].get('failed') or 'the suite'} is red on the target branch "
                    f"({f['baseline'].get('log')}); fix it first")

    ws = f["workspace"]
    if ws.get("on_branch") and ws.get("rebase"):
        return _rebase_in_progress(f)

    start = _cycle_start(f)
    rounds_done = [s["round"] for s in f["build"]]
    human = f["human_approval"] and f["human_approval"]["head"] in _equivalent_heads(f, f["head"])
    rework_pending = start > 1 and not _sections(f, start)
    if rework_pending:
        return _builder(f, "building", start, 1, "rework requested")

    current = max([n for n in rounds_done if n >= start], default=None)
    if not human:
        rebase_round = _latest_rebase(f)
        if rebase_round and rebase_round.get("after") == f["head"] and rebase_round.get("result") in ("red", "resolved") \
                and not any(n >= rebase_round.get("round", 0) for n in rounds_done):
            return _builder(f, "building", rebase_round["round"], 1, f"rebase left the checks {rebase_round['result']}")
        if current is None:
            return _builder(f, "building", start, 1, "first round")
        result = _round(f, current, start)
        if result is not None:
            return result
    return _approval(f, human)


def _latest_rebase(f):
    history = f["rebase"].get("history", [])
    return history[-1] if history else None


def _verified(f, record, heads):
    """A verify result counts only for the same code and the same checks."""
    return bool(record) and record.get("head") in heads and record.get("fingerprint") == f["verify_fingerprint"]


def _verified_build(f, facts, n, a, heads):
    ws = f["workspace"]
    if ws.get("on_branch") and ws.get("dirty"):
        return _builder(f, "building", n, a + 1, "the builder left uncommitted changes")
    verify = facts["verifies"].get(a)
    if not _verified(f, verify, heads):
        if not ws.get("on_branch"):
            return _out(f, "building", "open-workspace", "verify the round")
        return _out(f, "building", "verify", "run the project's tests, build and lint on the builder's result",
                    round=n, attempt=a)
    if not verify.get("green"):
        return _builder(f, "building", n, a + 1,
                        f"{verify.get('failed') or 'the checks'} is red after the builder ({verify.get('log')})")
    return None


def _tidied(f, facts, n, a, tidy, heads):
    """The tidy pass changes no behaviour, so anything short of a clean, green result is discarded and
    the builder's result goes to review as it was."""
    ws = f["workspace"]

    def discard(why):
        return _out(f, "building", "tidy-discard", why, round=n, before=tidy["before"])

    if tidy["status"] != "done":
        return discard("the tidy pass did not finish")
    if ws.get("on_branch") and ws.get("dirty") and not facts["scheduled"]:
        return discard("the tidy pass left uncommitted changes")
    if f["head"] == tidy["before"]:
        return _verified_build(f, facts, n, a, heads)
    verify = facts["verifies"].get("tidy")
    if not _verified(f, verify, heads):
        if not ws.get("on_branch"):
            return _out(f, "building", "open-workspace", "verify the tidy pass")
        return _out(f, "building", "verify", "run the project's tests, build and lint on the tidy pass",
                    round=n, attempt="tidy")
    if not verify.get("green"):
        return discard(f"{verify.get('failed') or 'the checks'} is red after the tidy pass ({verify.get('log')})")
    return None


def _round(f, n, start):
    sections = _sections(f, n)
    last = sections[-1]
    a = last["attempt"]
    facts = f["rounds"].get(n, {"scheduled": None, "reviews": {}, "rulings": {}, "verifies": {}})
    ws = f["workspace"]

    if last["status"] is None:
        return _builder(f, "building", n, a + 1, "the previous builder attempt did not finish")

    if last["status"] == "objection":
        ruling = facts["rulings"].get(a)
        if ruling is None:
            return _out(f, "building", "judge-objection", "the builder reports a departure from the plan",
                        round=n, attempt=a)
        if ruling == "NEEDS-HUMAN":
            return _out(f, "needs-human", "needs-human",
                        f"objection in round {n} needs your decision: it would change scope, design or acceptance criteria",
                        question=True, round=n, attempt=a)
        if ruling == "ANSWERED":
            return _builder(f, "building", n, a + 1, "you answered the objection; it is now in spec.md's Decided")
        return _builder(f, "building", n, a + 1, "objection ruled on; continue within the approved plan")

    heads = _equivalent_heads(f, f["head"])
    sched = facts["scheduled"]
    tidy = facts.get("tidy") if n == 1 else None
    if tidy and not tidy["discarded"]:
        result = _tidied(f, facts, n, a, tidy, heads)
    else:
        result = _verified_build(f, facts, n, a, heads)
        if result is None and n == 1 and not tidy and not sched:
            result = _out(f, "building", "spawn-tidy" if ws.get("on_branch") else "open-workspace",
                          "simplify and clean up the change once, before review", round=n)
    if result is not None:
        return result

    if not sched:
        return _out(f, "reviewing", "schedule", "pick this round's reviewers", round=n)
    if sched["head"] not in heads:
        return _out(f, "reviewing", "review-reset",
                    "the branch moved after reviewers were scheduled; discard the round's reviews", round=n)
    missing = [r for r in sched["reviewers"] if not (facts["reviews"].get(r) or {}).get("verdict")]
    if missing:
        rejected = {r: facts["reviews"][r]["problem"] for r in missing if (facts["reviews"].get(r) or {}).get("problem")}
        stuck = [r for r in missing if _stuck(f, f"review-{r}", n)]
        if stuck:
            why = "; ".join(f"{r}: {rejected[r]}" for r in stuck if r in rejected)
            return _out(f, "needs-human", "needs-human",
                        f"{', '.join(stuck)} spawned {STUCK_REPEATS} times in round {n} without a readable review"
                        + (f" ({why})" if why else ""))
        if not ws.get("on_branch"):
            return _out(f, "reviewing", "open-workspace", "reviewers read the workspace")
        if rejected:
            return _out(f, "reviewing", "spawn-reviewers",
                        "rewrite rejected review: " + "; ".join(f"{r} — {p}" for r, p in rejected.items()),
                        round=n, reviewers=missing)
        return _out(f, "reviewing", "spawn-reviewers", "waiting for every scheduled reviewer",
                    round=n, reviewers=missing)
    if ws.get("on_branch") and ws.get("dirty"):
        return _out(f, "reviewing", "review-reset", "the workspace changed during review", round=n)

    verdicts = _effective_verdicts(f, n, start)
    unreviewed = [r for r in f["required"] if r not in verdicts]
    if unreviewed:
        return _out(f, "reviewing", "review-reset", f"reviewers never ran: {', '.join(unreviewed)}", round=n)
    changes = sorted(r for r, v in verdicts.items() if v == "CHANGES")
    if changes:
        if n - start + 1 >= f["max_rounds"]:
            return _out(f, "needs-human", "needs-human",
                        f"blocking findings from {', '.join(changes)} after {f['max_rounds']} rounds")
        return _builder(f, "building", n + 1, 1, f"blocking findings from {', '.join(changes)}")
    return None


def _approval(f, human):
    if not human:
        return _out(f, "needs-human", "needs-human", READY, ready=True)

    pr = f["pr"]
    last = _latest_rebase(f)
    rebased = last and last.get("after") == f["head"] and last.get("result") == "green" \
        and last.get("fingerprint") == f["verify_fingerprint"]
    current = rebased and last.get("target") == f["target_head"]
    if pr and pr.get("state") in ("CLOSED", "ABANDONED"):
        return _out(f, "needs-human", "needs-human", f"the PR was closed without merging: {pr.get('url')}")
    if pr and pr.get("state") in ("OPEN", "MANUAL") and pr.get("head") == f["head"] and current:
        return _out(f, "pr-open", "wait-pr", f"PR open: {pr.get('url')}", url=pr.get("url"))
    if not current:
        why = "the target branch moved; rebase the open PR's branch and check it again" if pr \
            else "rebase on the target branch and run the project's checks"
        if not f["workspace"].get("on_branch"):
            return _out(f, "rebasing", "open-workspace", why)
        return _out(f, "rebasing", "rebase", why)
    if f["merge_mode"] == "local-merge":
        blocked = f["merge"] and f["merge"].get("blocked") and f["merge"].get("head") == f["head"]
        if blocked:
            return _out(f, "needs-human", "needs-human", f["merge"]["blocked"])
        return _out(f, "merging", "merge-local", "merge into the local target branch")
    return _out(f, "merging", "pr", "push the branch and open or update the PR")


def _rebase_in_progress(f):
    unmerged = f["workspace"].get("unmerged", [])
    last = _latest_rebase(f) or {}
    n = last.get("round") or (max([s["round"] for s in f["build"]], default=0) + 1)
    if unmerged:
        attempt = max([s["attempt"] for s in _sections(f, n)], default=0) + 1
        return _builder(f, "rebasing", n, attempt, f"rebase conflicts: {', '.join(unmerged)}")
    return _out(f, "rebasing", "rebase-continue", "conflicts resolved; continue the rebase")


def checks(paths, cfg, head):
    """The facts a human weighs before approving, computed rather than judged."""
    base = gitutil.merge_base(cfg.base_ref, head, paths.root)
    changed = gitutil.changed_files(base, head, paths.root)
    return {"diff_lines": gitutil.diff_size(base, head, paths.root), "max_diff": cfg.max_diff,
            "files": len(changed),
            "lockfiles": [c for c in changed if c in cfg.lockfiles or Path(c).name in cfg.lockfiles]}


def jira_todo(f, result):
    if not f["jira_enabled"]:
        return []
    todo = []
    stage = result["stage"]
    status_stage = {"building": "building", "reviewing": "building", "rebasing": "building", "merging": "building",
                    "needs-human": "needs-human", "pr-open": "pr-open", "merged": "merged", "done": "merged",
                    "cancelled": "cancelled"}.get(stage)
    log = f["jira_log"]
    last_status = next((e for e in reversed(log) if e.startswith("status:")), None)
    if status_stage and f["jira_map"].get(status_stage) and last_status != f"status:{status_stage}":
        todo.append({"action": "transition", "event": f"status:{status_stage}", "to": f["jira_map"][status_stage]})
    if stage == "cancelled" and "unassigned" not in log:
        todo.append({"action": "unassign", "event": "unassigned"})
    if stage in ("pr-open", "merged", "done") and "comment" not in log:
        todo.append({"action": "comment", "event": "comment"})
    return todo


PASS_THROUGH = ("cancel", "finish", "needs-human", "wait-pr", "none", "take", "start")


def hold_for_runs(f, result):
    """Live agent runs (see runs.py) hold the job: while its builder or tidy pass runs, nothing else
    happens to the workspace; a reviewer already running is not started again; and while any reviewer
    runs, its round isn't judged — it may still rewrite the file it already wrote."""
    nxt = result["next"]
    live = f.get("live_runs") or []
    writer = next((r for r in live if r["role"] in ("builder", "tidy")), None)
    if writer and nxt["action"] not in PASS_THROUGH:
        result["next"] = {"action": "wait-agent", "run": writer["run"]}
        result["reason"] = f"{writer['role']} run {writer['run']} is {writer['status']}"
        return result
    reviewers = [r for r in live if r["role"].startswith("review-")]
    busy = {r["role"][len("review-"):] for r in reviewers}
    if nxt["action"] == "spawn-reviewers":
        busy = {r["role"][len("review-"):] for r in reviewers if r["round"] == nxt["round"]}
        left = [n for n in nxt["reviewers"] if n not in busy]
        if left:
            nxt["reviewers"] = left
            return result
    if busy and nxt["action"] not in ("cancel", "finish"):
        result.update(stage="reviewing", waiting=False, reason=f"reviewers running: {', '.join(sorted(busy))}",
                      next={"action": "wait-agent", "runs": sorted(r["run"] for r in reviewers)})
    return result


def state(paths, cfg, job_id):
    jobs.check_id(job_id)
    f = gather(paths, cfg, job_id)
    f["live_runs"] = runs.for_job(paths, cfg, job_id)
    result = hold_for_runs(f, decide(f))
    if result["next"].get("ready") and f["head"]:
        c = checks(paths, cfg, f["head"])
        result["checks"] = c
        over = " — OVER" if c["diff_lines"] > c["max_diff"] else ""
        result["reason"] = (f"{READY} · diff {c['diff_lines']} lines in {c['files']} files (max_diff "
                            f"{c['max_diff']}{over})" + (f" · lockfiles: {', '.join(c['lockfiles'])}" if c["lockfiles"] else ""))
    result["round"] = max([s["round"] for s in f["build"]], default=0)
    result["head"] = f["head"]
    result["verdicts"] = {str(n): {**{name: None for name in (r["scheduled"] or {}).get("reviewers", [])},
                                   **{name: v["verdict"] for name, v in r["reviews"].items()}}
                          for n, r in sorted(f["rounds"].items())}
    result["fallback_used"] = any(v.get("fallback") for r in f["rounds"].values() for v in r["reviews"].values())
    result["runs"] = [{"role": r["role"], "round": r["round"], "status": r["status"],
                       "since": r.get("started_at") or r["created_at"]} for r in f["live_runs"]]
    result["tidy"] = (f["rounds"].get(1) or {}).get("tidy")
    result["pr"] = f["pr"]
    result["jira_todo"] = jira_todo(f, result)
    result["backlog"] = f["backlog"]
    return result


__all__ = ["state", "gather", "decide", "checks"]
