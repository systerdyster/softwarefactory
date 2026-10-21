"""`state <job-id>`: where a job stands and what happens next, derived only from the job folder,
backlog.md and git. The orchestrator asks this before every action.

gather() collects facts; decide() is a pure function of those facts, so the rules are testable
without git. Network state (PR status) is read from pr.json, which `pr status` refreshes."""

import json

from . import gitutil, jobs, queue, testfiles
from .config import ALWAYS_REVIEWERS
from .paths import branch_name

MAX_ATTEMPTS = 3
STUCK_REPEATS = 3
WAITING_STAGES = ("needs-human", "pr-open")
FINAL_STAGES = ("done", "cancelled")


def _round_facts(paths, job_id, n):
    d = paths.round_dir(job_id, n)
    replies, rulings, verifies = {}, {}, {}
    for f in d.glob("objection-reply-*.md"):
        replies[int(f.stem.rsplit("-", 1)[1])] = jobs.response(f.read_text(encoding="utf-8"))
    for f in d.glob("ruling-*.md"):
        rulings[int(f.stem.rsplit("-", 1)[1])] = jobs.ruling(f.read_text(encoding="utf-8"))
    for f in d.glob("verify-*.json"):
        verifies[int(f.stem.rsplit("-", 1)[1])] = jobs.read_json(f)
    return {"scheduled": jobs.scheduled(d), "reviews": jobs.reviews(d),
            "replies": replies, "rulings": rulings, "verifies": verifies}


def gather(paths, cfg, job_id):
    jd = paths.job(job_id)
    branch = branch_name(job_id)
    head = gitutil.rev(branch, paths.root)
    ws = {"exists": gitutil.is_worktree(paths.workspace), "on_branch": False}
    if ws["exists"]:
        rebasing = gitutil.rebase_in_progress(paths.workspace)
        ws["branch"] = gitutil.rebasing_branch(paths.workspace) if rebasing else gitutil.current_branch(paths.workspace)
        ws["on_branch"] = ws["branch"] == branch
        if ws["on_branch"]:
            ws["rebase"] = rebasing
            ws["dirty"] = [] if rebasing else gitutil.dirty_files(paths.workspace)
            ws["unmerged"] = gitutil.unmerged_files(paths.workspace) if rebasing else []
    test_rows = testfiles.listed(paths, job_id)
    recorded = testfiles.is_recorded(paths, job_id)
    tests_ok, mismatches = None, []
    if recorded and head and not ws.get("rebase"):
        result = testfiles.check(paths, job_id)
        tests_ok, mismatches = result["ok"], [m["path"] for m in result["mismatches"]]
    tests_text = jobs.read(jd / "tests.md")
    decision_text = jobs.read(jd / "decision.md")
    human_text = jobs.read(jd / "human-approval.md")
    rounds = {n: _round_facts(paths, job_id, n) for n in jobs.rounds_on_disk(jd)}
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
        "tests_md": tests_text is not None,
        "tests_status": (jobs.field(tests_text, "Status") or "").lower().split()[0] if tests_text and jobs.field(tests_text, "Status") else None,
        "test_files": [r["path"] for r in test_rows],
        "tests_recorded": recorded,
        "tests_ok": tests_ok,
        "test_mismatches": mismatches,
        "build": jobs.build_sections(jobs.read(jd / "build.md")),
        "rounds": rounds,
        "reworks": jobs.reworks(jd),
        "decision": {"verdict": jobs.decision(decision_text), "head": jobs.field(decision_text, "Head")} if decision_text else None,
        "human_approval": {"head": jobs.field(human_text, "Head")} if human_text else None,
        "rebase": jobs.read_json(jd / "rebase.json") or {"history": []},
        "pr": jobs.read_json(jd / "pr.json"),
        "merge": jobs.read_json(jd / "merge.json"),
        "merged_locally": bool(head) and (jd / "merge.json").exists() and gitutil.rev(cfg.target_branch, paths.root) is not None
        and gitutil.is_ancestor(head, cfg.target_branch, paths.root),
        "spawns": _spawn_log(jd),
        "jira_log": _jira_log(jd),
        "enabled": cfg.enabled_reviewers,
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
        return _out(f, stage, "switch", reason)
    return _out(f, stage, "spawn-builder", reason, round=n, attempt=attempt)


def _test_writer(f, stage, mode, reason, n=None, attempt=None):
    if _stuck(f, "test-writer", n):
        return _out(f, "needs-human", "needs-human",
                    f"test-writer spawned {STUCK_REPEATS} times ({mode}) without progress")
    if not f["workspace"].get("on_branch"):
        return _out(f, stage, "switch", reason)
    return _out(f, stage, "spawn-test-writer", reason, mode=mode, round=n, attempt=attempt)


def decide(f):
    if (f["decision"] and f["decision"]["verdict"] == "CANCELLED") or f["backlog"] == "cancelled":
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
        return _out(f, "starting", "start", "switch from the target branch and prove the suite green")
    if not f["baseline"].get("green"):
        if f["baseline"].get("target") and f["baseline"].get("target") != f["target_head"]:
            return _out(f, "starting", "start", "the target branch moved since its suite was red; try again")
        return _out(f, "needs-human", "needs-human",
                    f"the full suite is red on the target branch ({f['baseline'].get('log')}); fix it first")

    ws = f["workspace"]
    if ws.get("on_branch") and ws.get("rebase"):
        return _rebase_in_progress(f)

    if not f["tests_recorded"]:
        if f["tests_md"] and f["tests_status"] == "red-confirmed":
            return _out(f, "testing", "tests-record", "test-writer finished; pin the test files")
        return _test_writer(f, "testing", "write", "write the failing tests the plan names", n=0)

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
            return _builder(f, "building", rebase_round["round"], 1, f"rebase left the suite {rebase_round['result']}")
        if current is None:
            return _builder(f, "building", start, 1, "first round")
        result = _round(f, current, start)
        if result is not None:
            return result
    return _approval(f, human)


def _latest_rebase(f):
    history = f["rebase"].get("history", [])
    return history[-1] if history else None


def _round(f, n, start):
    sections = _sections(f, n)
    last = sections[-1]
    a = last["attempt"]
    facts = f["rounds"].get(n, {"scheduled": None, "reviews": {}, "replies": {}, "rulings": {}, "verifies": {}})
    ws = f["workspace"]

    if last["status"] is None:
        return _builder(f, "building", n, a + 1, "the previous builder attempt did not finish")
    if f["tests_ok"] is False and not (last["status"] == "objection" and facts["replies"].get(a) == "AMENDED"):
        return _out(f, "building", "tests-restore",
                    f"test files changed outside test-writer: {', '.join(f['test_mismatches'])}", round=n)

    if last["status"] == "objection":
        reply = facts["replies"].get(a)
        ruling = facts["rulings"].get(a)
        if last["objection"] == "test" and reply is None:
            return _test_writer(f, "building", "objection", "the builder disputes a test", n=n, attempt=a)
        if reply == "AMENDED":
            if f["tests_ok"] is False:
                return _out(f, "building", "tests-record", "test-writer amended the disputed tests", round=n)
            return _builder(f, "building", n, a + 1, "test-writer amended the disputed tests")
        if ruling is None:
            return _out(f, "building", "judge-objection",
                        "test-writer stands firm" if reply == "STANDS" else "the builder reports a departure from the plan",
                        round=n, attempt=a)
        if ruling == "NEEDS-HUMAN":
            return _out(f, "needs-human", "needs-human",
                        f"objection in round {n} would change scope, design or acceptance criteria")
        return _builder(f, "building", n, a + 1, "objection ruled on; continue within the approved plan")

    if ws.get("on_branch") and ws.get("dirty"):
        return _builder(f, "building", n, a + 1, "the builder left uncommitted changes")
    heads = _equivalent_heads(f, f["head"])
    verify = facts["verifies"].get(a)
    if not verify or verify.get("head") not in heads:
        if not ws.get("on_branch"):
            return _out(f, "building", "switch", "verify the round")
        return _out(f, "building", "verify", "run the full suite on the builder's result", round=n, attempt=a)
    if not verify.get("green"):
        return _builder(f, "building", n, a + 1, f"the full suite is red after the builder ({verify.get('log')})")

    sched = facts["scheduled"]
    if not sched:
        return _out(f, "reviewing", "schedule", "pick this round's reviewers", round=n)
    if sched["head"] not in heads:
        return _out(f, "reviewing", "review-reset",
                    "the branch moved after reviewers were scheduled; discard the round's reviews", round=n)
    missing = [r for r in sched["reviewers"] if not (facts["reviews"].get(r) or {}).get("verdict")]
    if missing:
        stuck = [r for r in missing if _stuck(f, f"review-{r}", n)]
        if stuck:
            return _out(f, "needs-human", "needs-human",
                        f"{', '.join(stuck)} spawned {STUCK_REPEATS} times in round {n} without writing a review")
        if not ws.get("on_branch"):
            return _out(f, "reviewing", "switch", "reviewers read the workspace")
        return _out(f, "reviewing", "spawn-reviewers", "waiting for every scheduled reviewer",
                    round=n, reviewers=missing)
    if ws.get("on_branch") and ws.get("dirty"):
        return _out(f, "reviewing", "review-reset", "the workspace changed during review", round=n)

    verdicts = _effective_verdicts(f, n, start)
    unreviewed = [r for r in f["enabled"] if r not in verdicts]
    if unreviewed:
        return _out(f, "reviewing", "review-reset", f"reviewers never ran: {', '.join(unreviewed)}", round=n)
    changes = sorted(r for r, v in verdicts.items() if v == "CHANGES")
    if changes:
        if n - start + 1 >= f["max_rounds"]:
            return _out(f, "needs-human", "needs-human",
                        f"CHANGES from {', '.join(changes)} after {f['max_rounds']} rounds")
        return _builder(f, "building", n + 1, 1, f"CHANGES from {', '.join(changes)}")
    return None


def _approval(f, human):
    heads = _equivalent_heads(f, f["head"])
    d = f["decision"]
    approved = human or (d and d["verdict"] == "APPROVE" and d["head"] in heads)
    if not approved:
        if d and d["verdict"] == "ESCALATE" and d["head"] in heads:
            return _out(f, "needs-human", "needs-human", "the approver escalated; see decision.md")
        if _stuck(f, "approver", None):
            return _out(f, "needs-human", "needs-human",
                        f"the approver ran {STUCK_REPEATS} times without a decision for the current head")
        if not f["workspace"].get("on_branch"):
            return _out(f, "approving", "switch", "the approver reads the workspace")
        return _out(f, "approving", "spawn-approver", "every reviewer passed")

    pr = f["pr"]
    last = _latest_rebase(f)
    rebased = last and last.get("after") == f["head"] and last.get("result") == "green"
    if pr and pr.get("state") in ("OPEN", "MANUAL") and pr.get("head") == f["head"]:
        return _out(f, "pr-open", "wait-pr", f"PR open: {pr.get('url')}", url=pr.get("url"))
    if pr and pr.get("state") in ("CLOSED", "ABANDONED"):
        return _out(f, "needs-human", "needs-human", f"the PR was closed without merging: {pr.get('url')}")
    if not rebased or last.get("target") != f["target_head"]:
        if not f["workspace"].get("on_branch"):
            return _out(f, "rebasing", "switch", "rebase on the target branch")
        return _out(f, "rebasing", "rebase", "rebase on the target branch and run the full suite")
    if f["merge_mode"] == "local-merge":
        blocked = f["merge"] and f["merge"].get("blocked") and f["merge"].get("head") == f["head"]
        if blocked:
            return _out(f, "needs-human", "needs-human", f["merge"]["blocked"])
        return _out(f, "merging", "merge-local", "merge into the local target branch")
    return _out(f, "merging", "pr", "push the branch and open or update the PR")


def _rebase_in_progress(f):
    unmerged = f["workspace"].get("unmerged", [])
    tests = [p for p in unmerged if p in f["test_files"]]
    other = [p for p in unmerged if p not in f["test_files"]]
    last = _latest_rebase(f) or {}
    n = last.get("round") or (max([s["round"] for s in f["build"]], default=0) + 1)
    if tests:
        return _test_writer(f, "rebasing", "rebase-conflict",
                            f"rebase conflicts in test-writer's files: {', '.join(tests)}", n=n)
    if other:
        attempt = max([s["attempt"] for s in _sections(f, n)], default=0) + 1
        return _builder(f, "rebasing", n, attempt, f"rebase conflicts: {', '.join(other)}")
    return _out(f, "rebasing", "rebase-continue", "conflicts resolved; continue the rebase")


def jira_todo(f, result):
    if not f["jira_enabled"]:
        return []
    todo = []
    stage = result["stage"]
    status_stage = {"building": "building", "testing": "building", "reviewing": "building", "approving": "building",
                    "rebasing": "building", "merging": "building", "needs-human": "needs-human",
                    "pr-open": "pr-open", "merged": "merged", "done": "merged", "cancelled": "cancelled"}.get(stage)
    log = f["jira_log"]
    last_status = next((e for e in reversed(log) if e.startswith("status:")), None)
    if status_stage and f["jira_map"].get(status_stage) and last_status != f"status:{status_stage}":
        todo.append({"action": "transition", "event": f"status:{status_stage}", "to": f["jira_map"][status_stage]})
    if stage == "cancelled" and "unassigned" not in log:
        todo.append({"action": "unassign", "event": "unassigned"})
    if stage in ("pr-open", "merged", "done") and "comment" not in log:
        todo.append({"action": "comment", "event": "comment"})
    return todo


def state(paths, cfg, job_id):
    jobs.check_id(job_id)
    f = gather(paths, cfg, job_id)
    result = decide(f)
    result["round"] = max([s["round"] for s in f["build"]], default=0)
    result["head"] = f["head"]
    result["verdicts"] = {str(n): {**{name: None for name in (r["scheduled"] or {}).get("reviewers", [])},
                                   **{name: v["verdict"] for name, v in r["reviews"].items()}}
                          for n, r in sorted(f["rounds"].items())}
    result["fallback_used"] = any(v.get("fallback") for r in f["rounds"].values() for v in r["reviews"].values())
    result["pr"] = f["pr"]
    result["jira_todo"] = jira_todo(f, result)
    result["backlog"] = f["backlog"]
    return result


__all__ = ["state", "gather", "decide", "ALWAYS_REVIEWERS"]
