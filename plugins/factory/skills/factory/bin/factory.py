#!/usr/bin/env python3
"""The software factory's mechanical parts. Every subcommand prints JSON and exits non-zero on
failure (1 refused or failed, 3 codex fallback). Python standard library only.

    factory-cli <command> [args]        (the plugin puts factory-cli on PATH)

Run with `help` for the command list. The orchestrator calls these and trusts their output."""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fx import actions, board, brief, codex, config, guard, jobs, pr, queue, schedule, setup, testfiles, workspace  # noqa: E402
from fx import state as state_mod  # noqa: E402
from fx.errors import Fallback, FactoryError  # noqa: E402
from fx.paths import Paths  # noqa: E402


def _cfg(paths):
    return config.load(paths)


def _state_round(paths, cfg, job_id):
    s = state_mod.state(paths, cfg, job_id)
    n = s["next"].get("round")
    if n is None:
        raise FactoryError(f"state names no round for {s['next']['action']}", state=s)
    return s, n


def cmd_state(a, p):
    return state_mod.state(p, _cfg(p), a.job)


def cmd_board(a, p):
    return board.build(p, _cfg(p), write=not a.print)


def cmd_schedule(a, p):
    cfg = _cfg(p)
    s, n = _state_round(p, cfg, a.job)
    if s["next"]["action"] != "schedule":
        raise FactoryError(f"state says {s['next']['action']}, not schedule", state=s)
    starts = [r["start_round"] for r in jobs.reworks(p.job(a.job)) if r["start_round"]]
    return schedule.schedule(p, cfg, a.job, n, max(starts) if starts else 1)


def cmd_tests(a, p):
    if a.action == "record":
        return testfiles.record(p, a.job)
    if a.action == "check":
        result = testfiles.check(p, a.job)
        if not result["ok"]:
            raise FactoryError("test files differ from their recorded hashes", **result)
        return result
    return testfiles.restore(p, a.job)


def cmd_queue(a, p):
    if a.action == "add":
        branch = queue.branch_for(_cfg(p).branch_pattern, a.job, a.title, a.jira)
        return queue.add(p, a.job, a.title, jira=a.jira, developer=a.developer, branch=branch,
                         work_item=a.work_item)
    if a.action == "take":
        return {"taken": queue.take(p)}
    if a.action == "mark":
        return queue.mark(p, a.job, a.status)
    if a.action == "next-id":
        return {"id": queue.next_id(p, a.slug)}
    return {"entries": queue.entries(p)}


def cmd_workspace(a, p):
    cfg = _cfg(p)
    if a.action == "create":
        return workspace.create(p, cfg)
    if a.action == "switch":
        return workspace.switch(p, cfg, a.job)
    if a.action == "check":
        result = workspace.check(p)
        if not result["clean"]:
            raise FactoryError("the workspace has changed since the last commit", **result)
        return result
    if a.action == "setup":
        return workspace.setup(p, cfg)
    return workspace.detach(p, cfg, discard=a.discard)


def cmd_ws(a, p):
    result = workspace.run(p, _cfg(p), a.name, a.args)
    if result["exit_code"] != 0:
        raise FactoryError(f"{a.name} failed with exit code {result['exit_code']}", **result)
    return result


def cmd_suite(a, p):
    result = workspace.suite(p, _cfg(p))
    if not result["green"]:
        raise FactoryError("the full suite is red", **result)
    return result


def cmd_start(a, p):
    return actions.start(p, _cfg(p), a.job)


def cmd_verify(a, p):
    cfg = _cfg(p)
    s, n = _state_round(p, cfg, a.job)
    if s["next"]["action"] != "verify":
        raise FactoryError(f"state says {s['next']['action']}, not verify", state=s)
    result = actions.verify(p, cfg, a.job, n, s["next"]["attempt"])
    if not result["green"]:
        raise FactoryError("the full suite is red after the builder", **result)
    return result


def cmd_review_reset(a, p):
    cfg = _cfg(p)
    s, n = _state_round(p, cfg, a.job)
    return actions.review_reset(p, a.job, n)


def cmd_codex_review(a, p):
    cfg = _cfg(p)
    s, n = _state_round(p, cfg, a.job)
    return codex.review(p, cfg, a.job, n)


def cmd_brief(a, p):
    text = brief.brief(p, _cfg(p), a.job, a.agent, a.reviewer)
    return {"brief": text}


def cmd_rebase(a, p):
    return actions.rebase(p, _cfg(p), a.job, continue_=a.cont)


def cmd_pr(a, p):
    cfg = _cfg(p)
    if a.action == "open":
        return pr.open_or_update(p, cfg, a.job)
    if a.action == "status":
        return pr.status(p, cfg, a.job)
    if a.action == "comments":
        return {"comments": pr.comments(p, cfg, a.job)}
    if a.action == "body":
        return {"body": pr.body(p, cfg, a.job)}
    return pr.close(p, cfg, a.job)


def cmd_merge(a, p):
    return pr.merge_local(p, _cfg(p), a.job)


def cmd_cleanup(a, p):
    return actions.cleanup(p, _cfg(p), a.job, discard=a.discard)


def cmd_finish(a, p):
    return actions.finish(p, _cfg(p), a.job)


def cmd_cancel(a, p):
    return actions.cancel(p, _cfg(p), a.job, a.reason, pr)


def cmd_approve(a, p):
    return actions.approve(p, _cfg(p), a.job, a.by)


def cmd_rework(a, p):
    return actions.rework(p, _cfg(p), a.job, a.note, a.by, pr)


def cmd_next(a, p):
    return actions.next_job(p, _cfg(p))


def cmd_stop(a, p):
    return actions.stop(p, clear=a.clear)


def cmd_command(a, p):
    return actions.write_command(p, a.command, a.job, a.text, a.by)


def cmd_inbox(a, p):
    if a.action == "done":
        return actions.inbox_done(p, a.file, a.outcome)
    if a.action == "watch":
        actions.inbox_watch(p)
    return {"pending": actions.inbox_list(p)}


def cmd_jira_log(a, p):
    return actions.jira_log(p, a.job, a.event, a.detail or "")


def cmd_init_files(a, p):
    return setup.init_files(p)


def cmd_allowlist(a, p):
    return setup.allowlist(p, None, write=a.write)


def cmd_guard(a, p):
    sys.exit(guard.run(a.role, paths=p))


def parser():
    ap = argparse.ArgumentParser(prog="factory.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(fn=fn)
        return sp

    add("state", cmd_state, "where a job stands and the next action").add_argument("job")
    add("board", cmd_board, "rebuild board.json from job folders and git").add_argument("--print", action="store_true")
    add("schedule", cmd_schedule, "write round-N/scheduled.md").add_argument("job")
    sp = add("tests", cmd_tests, "record|check|restore test-writer's file hashes")
    sp.add_argument("action", choices=["record", "check", "restore"])
    sp.add_argument("job")
    sp = add("queue", cmd_queue, "add|take|mark|list|next-id on backlog.md, under a lock")
    qs = sp.add_subparsers(dest="action", required=True)
    q = qs.add_parser("add")
    q.add_argument("job")
    q.add_argument("--title", required=True)
    q.add_argument("--jira")
    q.add_argument("--developer")
    q.add_argument("--work-item", dest="work_item", help="Azure Boards work item id to link to the PR")
    qs.add_parser("take")
    q = qs.add_parser("mark")
    q.add_argument("job")
    q.add_argument("status", choices=list(queue.MARKS))
    qs.add_parser("list")
    qs.add_parser("next-id").add_argument("slug")
    sp = add("workspace", cmd_workspace, "create|switch|check|setup|detach the persistent worktree")
    sp.add_argument("action", choices=["create", "switch", "check", "setup", "detach"])
    sp.add_argument("job", nargs="?")
    sp.add_argument("--discard", action="store_true")
    sp = add("ws", cmd_ws, "run a named project command in the workspace: ws run <name> [args]")
    sp.add_argument("run", choices=["run"])
    sp.add_argument("name")
    sp.add_argument("args", nargs=argparse.REMAINDER)
    add("suite", cmd_suite, "run the full suite in the workspace")
    add("start", cmd_start, "switch a taken job's branch from the target and prove the suite green").add_argument("job")
    add("verify", cmd_verify, "run the full suite on the builder's round").add_argument("job")
    add("review-reset", cmd_review_reset, "discard the current round's reviews and workspace changes").add_argument("job")
    add("codex-review", cmd_codex_review, "run Codex for the current round; exit 3 means spawn codex-fallback").add_argument("job")
    sp = add("brief", cmd_brief, "the prompt for a fresh agent, derived from state")
    sp.add_argument("job")
    sp.add_argument("agent", choices=sorted(brief.EXPECTED))
    sp.add_argument("--reviewer")
    sp = add("rebase", cmd_rebase, "rebase on the target and run the suite; --continue after conflicts")
    sp.add_argument("job")
    sp.add_argument("--continue", dest="cont", action="store_true")
    sp = add("pr", cmd_pr, "open|status|comments|body|close the job's PR (pushes the branch)")
    sp.add_argument("action", choices=["open", "status", "comments", "body", "close"])
    sp.add_argument("job")
    add("merge", cmd_merge, "local-merge: --no-ff merge into the local target branch").add_argument("job")
    sp = add("cleanup", cmd_cleanup, "remove the job's local and remote branch; workspace back to target")
    sp.add_argument("job")
    sp.add_argument("--discard", action="store_true")
    add("finish", cmd_finish, "cleanup and mark [x] after merge").add_argument("job")
    sp = add("cancel", cmd_cancel, "close the PR, cleanup, mark [-], write the reason")
    sp.add_argument("job")
    sp.add_argument("--reason", default="")
    sp = add("approve", cmd_approve, "record a human approval of a needs-human job's head")
    sp.add_argument("job")
    sp.add_argument("--by", default="")
    sp = add("rework", cmd_rework, "queue a rework round with a note (and PR comments)")
    sp.add_argument("job")
    sp.add_argument("--note", required=True)
    sp.add_argument("--by", default="")
    add("next", cmd_next, "the job the orchestrator works on now (takes from the queue when idle)")
    add("stop", cmd_stop, "stop after the current job; --clear to lift").add_argument("--clear", action="store_true")
    sp = add("command", cmd_command, "write an approve|rework|cancel command to the inbox")
    sp.add_argument("command", choices=["approve", "rework", "cancel"])
    sp.add_argument("job")
    sp.add_argument("--text", default="")
    sp.add_argument("--by", default="")
    sp = add("inbox", cmd_inbox, "list pending commands, mark one done, or watch (one line per new command)")
    sp.add_argument("action", choices=["list", "done", "watch"])
    sp.add_argument("file", nargs="?")
    sp.add_argument("--outcome", default="")
    sp = add("jira-log", cmd_jira_log, "record a Jira action taken for a job")
    sp.add_argument("job")
    sp.add_argument("event")
    sp.add_argument("--detail")
    add("init-files", cmd_init_files, "create factory/, project.md template, backlog.md, .gitignore entry")
    add("allowlist", cmd_allowlist, "propose (or --write) the permission allowlist").add_argument("--write", action="store_true")
    add("guard", cmd_guard, "PreToolUse hook for factory agents").add_argument("role")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        paths = Paths()
        result = args.fn(args, paths)
    except Fallback as e:
        print(json.dumps({"fallback": True, "reason": str(e)}, indent=2))
        return 3
    except FactoryError as e:
        print(json.dumps({"error": str(e), **e.details}, indent=2, default=str))
        return 1
    if args.cmd == "brief":
        print(result["brief"])
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
