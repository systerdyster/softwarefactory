"""decide() is a pure function of the facts gather() collects, so each rule is tested by stating
the facts of one moment in a job's life and asserting the next action."""

import copy
import unittest

from support import SandboxTest

from fx import state, workspace

H1, H2, H3, T1, T2 = "a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40
FP = "f" * 16


def base_facts(**over):
    f = {
        "id": "OPT-1", "meta": {"title": "T", "case": "OPT-1"}, "has_spec": True, "backlog": "taken",
        "branch_exists": True, "head": H1, "target_head": T1,
        "workspace": {"exists": True, "on_branch": True, "dirty": [], "rebase": False, "unmerged": []},
        "baseline": {"green": True}, "verify_fingerprint": FP,
        "build": [], "rounds": {}, "reworks": [], "cancelled": False, "human_approval": None,
        "rebase": {"history": []}, "pr": None, "merge": None, "merged_locally": False, "spawns": [],
        "events_log": [], "required": ["code"], "max_rounds": 3, "merge_mode": "pr",
        "test_note": "", "test_note_file": "/jobs/OPT-1/test-note.md",
    }
    f.update(over)
    return f


def rnd(reviews=None, scheduled=None, head=H1, verifies=None, rulings=None):
    names = list((reviews or {}).keys()) if scheduled is None else scheduled
    return {"scheduled": {"reviewers": names, "head": head, "base": T1} if names or scheduled == [] else None,
            "reviews": {n: {"verdict": v, "problem": None, "fallback": False} for n, v in (reviews or {}).items()},
            "verifies": verifies if verifies is not None else {1: {"head": head, "green": True, "fingerprint": FP}},
            "rulings": rulings or {}}


def done(n, attempt=1):
    return {"round": n, "attempt": attempt, "status": "done", "objection": None}


def passing(head=H1):
    return rnd({"code": "PASS"}, head=head)


def tidied(before=H1, status="done", discarded=None):
    return {"before": before, "status": status, "discarded": discarded}


def nxt(f):
    return state.decide(f)["next"]["action"]


class LifecycleTest(unittest.TestCase):
    def test_queued_job_waits_for_take(self):
        f = base_facts(backlog="queued", baseline=None, branch_exists=False, head=None)
        self.assertEqual(nxt(f), "take")

    def test_taken_job_starts_by_proving_the_target_green(self):
        self.assertEqual(nxt(base_facts(baseline=None)), "start")

    def test_red_target_suite_stops_for_a_human(self):
        r = state.decide(base_facts(baseline={"green": False, "log": "x.log"}))
        self.assertEqual(r["stage"], "needs-human")
        self.assertIn("red on the target", r["reason"])

    def test_a_red_target_is_retried_once_the_target_moves(self):
        f = base_facts(baseline={"green": False, "target": T1, "log": "x"}, target_head=T2)
        self.assertEqual(nxt(f), "start")

    def test_a_job_without_its_spec_waits_for_a_human(self):
        self.assertEqual(state.decide(base_facts(has_spec=False))["stage"], "needs-human")

    def test_a_reviewer_that_never_writes_its_file_is_stopped(self):
        spawns = [{"agent": "review-code", "round": 1, "head": H1, "conflicts": []}] * 3
        f = base_facts(build=[done(1)], rounds={1: rnd({}, scheduled=["code"])}, spawns=spawns)
        self.assertEqual(state.decide(f)["stage"], "needs-human")

    def test_a_job_without_its_worktree_reopens_it(self):
        f = base_facts(workspace={"exists": False, "on_branch": False})
        self.assertEqual(nxt(f), "open-workspace")

    def test_the_builder_goes_first_with_no_test_writer(self):
        r = state.decide(base_facts())
        self.assertEqual((r["next"]["action"], r["next"]["round"], r["next"]["attempt"]), ("spawn-builder", 1, 1))
        self.assertEqual(r["stage"], "building")


class VerifyTest(unittest.TestCase):
    def test_the_round_is_verified_after_the_builder(self):
        f = base_facts(build=[done(1)], rounds={1: rnd(verifies={})})
        self.assertEqual(nxt(f), "verify")

    def test_a_result_for_other_checks_is_not_reused(self):
        f = base_facts(build=[done(1)], rounds={1: rnd()}, verify_fingerprint="0" * 16)
        self.assertEqual(nxt(f), "verify")

    def test_a_result_for_other_code_is_not_reused(self):
        f = base_facts(head=H2, build=[done(1)], rounds={1: rnd()})
        self.assertEqual(nxt(f), "verify")

    def test_the_red_check_is_named_to_the_builder(self):
        f = base_facts(build=[done(1)], rounds={1: rnd(verifies={1: {"head": H1, "green": False, "log": "l",
                                                                     "failed": "lint", "fingerprint": FP}})})
        self.assertIn("lint is red", state.decide(f)["reason"])

    def test_a_rebase_under_other_checks_is_run_again(self):
        f = base_facts(build=[done(1)], rounds={1: rnd({"code": "PASS"})}, human_approval={"head": H1},
                       rebase={"history": [{"before": H1, "after": H1, "result": "green", "target": T1,
                                            "fingerprint": "0" * 16}]})
        self.assertEqual(nxt(f), "rebase")


class RejectedReviewTest(unittest.TestCase):
    def test_an_unreadable_review_goes_back_to_its_reviewer_with_the_reason(self):
        r1 = rnd({"code": None}, scheduled=["code"])
        r1["reviews"]["code"]["problem"] = "line 1 says CHANGES but no finding is marked BLOCKING"
        r = state.decide(base_facts(build=[done(1)], rounds={1: r1}))
        self.assertEqual((r["next"]["action"], r["next"]["reviewers"]), ("spawn-reviewers", ["code"]))
        self.assertIn("no finding is marked BLOCKING", r["reason"])

    def test_it_never_counts_as_a_pass(self):
        r1 = rnd({"code": None}, scheduled=["code"])
        r1["reviews"]["code"]["problem"] = "x"
        self.assertNotEqual(state.decide(base_facts(build=[done(1)], rounds={1: r1}))["stage"], "needs-human")


class BuildRoundTest(unittest.TestCase):
    def test_unfinished_builder_is_respawned_with_the_next_attempt(self):
        f = base_facts(build=[{"round": 1, "attempt": 1, "status": None, "objection": None}])
        self.assertEqual(state.decide(f)["next"]["attempt"], 2)

    def test_three_failed_attempts_go_to_a_human(self):
        f = base_facts(build=[{"round": 1, "attempt": 3, "status": None, "objection": None}])
        self.assertEqual(state.decide(f)["stage"], "needs-human")

    def test_uncommitted_builder_work_is_not_reviewed(self):
        f = base_facts(build=[done(1)], workspace={"exists": True, "on_branch": True, "dirty": ["src/a.py"]})
        self.assertEqual(nxt(f), "spawn-builder")

    def test_red_verify_sends_the_builder_back_in_the_same_round(self):
        f = base_facts(build=[done(1)], rounds={1: rnd(verifies={1: {"head": H1, "green": False, "log": "l", "failed": "lint", "fingerprint": FP}})})
        r = state.decide(f)
        self.assertEqual((r["next"]["action"], r["next"]["round"], r["next"]["attempt"]), ("spawn-builder", 1, 2))

    def test_verified_round_is_scheduled_then_reviewed(self):
        f = base_facts(build=[done(1)], rounds={1: {**rnd(), "scheduled": None, "tidy": tidied()}})
        self.assertEqual(nxt(f), "schedule")
        f["rounds"][1] = rnd({}, scheduled=["code", "ui"])
        r = state.decide(f)
        self.assertEqual((r["next"]["action"], r["next"]["reviewers"]), ("spawn-reviewers", ["code", "ui"]))

    def test_only_missing_reviewers_are_spawned(self):
        f = base_facts(build=[done(1)], rounds={1: rnd({"code": "PASS"}, scheduled=["code", "ui"])})
        self.assertEqual(state.decide(f)["next"]["reviewers"], ["ui"])

    def test_a_moved_branch_discards_the_round(self):
        f = base_facts(build=[done(1)], rounds={1: passing()})
        f["rounds"][1]["scheduled"]["head"] = H2
        self.assertEqual(nxt(f), "review-reset")

    def test_blocking_findings_start_the_next_round(self):
        f = base_facts(build=[done(1)], rounds={1: rnd({"code": "PASS", "ui": "CHANGES"})})
        r = state.decide(f)
        self.assertEqual((r["next"]["action"], r["next"]["round"]), ("spawn-builder", 2))
        self.assertIn("ui", r["reason"])

    def test_earlier_pass_verdicts_stand(self):
        r1 = rnd({"code": "CHANGES", "ui": "PASS"})
        r2 = rnd({"code": "PASS"}, head=H2)
        f = base_facts(head=H2, build=[done(1), done(2)], rounds={1: r1, 2: r2})
        self.assertEqual(state.decide(f)["stage"], "needs-human")

    def test_blocking_at_max_rounds_goes_to_a_human(self):
        rounds = {n: rnd({"code": "CHANGES"}) for n in (1, 2, 3)}
        f = base_facts(build=[done(1), done(2), done(3)], rounds=rounds)
        r = state.decide(f)
        self.assertEqual(r["stage"], "needs-human")
        self.assertIn("after 3 rounds", r["reason"])


class TidyTest(unittest.TestCase):
    def round_one(self, head=H1, tidy=None, **over):
        return base_facts(head=head, build=[done(1)], rounds={1: {**rnd(**over), "scheduled": None, "tidy": tidy}})

    def test_round_one_is_tidied_once_after_its_first_green_verify(self):
        r = state.decide(self.round_one())
        self.assertEqual((r["next"]["action"], r["next"]["round"], r["stage"]), ("spawn-tidy", 1, "building"))

    def test_a_red_build_goes_back_to_the_builder_not_to_the_tidy_pass(self):
        f = self.round_one(verifies={1: {"head": H1, "green": False, "log": "l", "failed": "lint", "fingerprint": FP}})
        self.assertEqual(nxt(f), "spawn-builder")

    def test_later_rounds_and_reworks_are_not_tidied(self):
        f = base_facts(head=H2, build=[done(1), done(2)],
                       rounds={1: {**rnd({"code": "CHANGES"}), "tidy": None}, 2: {**rnd(head=H2), "scheduled": None}})
        self.assertEqual(nxt(f), "schedule")

    def test_a_tidied_head_is_verified_before_review(self):
        r = state.decide(self.round_one(head=H2, tidy=tidied()))
        self.assertEqual((r["next"]["action"], r["next"]["attempt"]), ("verify", "tidy"))

    def test_a_green_tidied_head_is_reviewed(self):
        verifies = {1: {"head": H1, "green": True, "fingerprint": FP}, "tidy": {"head": H2, "green": True, "fingerprint": FP}}
        self.assertEqual(nxt(self.round_one(head=H2, tidy=tidied(), verifies=verifies)), "schedule")

    def test_a_tidy_pass_that_changed_nothing_is_not_verified_again(self):
        self.assertEqual(nxt(self.round_one(tidy=tidied())), "schedule")

    def test_anything_short_of_a_clean_green_tidy_pass_is_discarded(self):
        red = {1: {"head": H1, "green": True, "fingerprint": FP},
               "tidy": {"head": H2, "green": False, "failed": "suite", "log": "l", "fingerprint": FP}}
        for f, why in ((self.round_one(head=H2, tidy=tidied(status=None)), "did not finish"),
                       (self.round_one(head=H2, tidy=tidied(), verifies=red), "suite is red after the tidy pass")):
            r = state.decide(f)
            self.assertEqual((r["next"]["action"], r["next"]["before"]), ("tidy-discard", H1))
            self.assertIn(why, r["reason"])
        f = self.round_one(head=H2, tidy=tidied())
        f["workspace"]["dirty"] = ["src/a.py"]
        self.assertEqual(nxt(f), "tidy-discard")

    def test_a_workspace_changed_during_review_resets_the_review_not_the_tidy_pass(self):
        verifies = {1: {"head": H1, "green": True, "fingerprint": FP}, "tidy": {"head": H2, "green": True, "fingerprint": FP}}
        f = base_facts(head=H2, build=[done(1)],
                       rounds={1: {**rnd({"code": "PASS"}, head=H2, verifies=verifies), "tidy": tidied()}})
        f["workspace"]["dirty"] = ["src/a.py"]
        self.assertEqual(nxt(f), "review-reset")

    def test_a_discarded_tidy_pass_reviews_the_builders_result(self):
        self.assertEqual(nxt(self.round_one(tidy=tidied(discarded="suite is red"))), "schedule")

    def test_a_live_tidy_pass_holds_the_job(self):
        f = self.round_one(head=H2, tidy=tidied(status=None))
        f["live_runs"] = [{"run": "t1", "role": "tidy", "round": 1, "attempt": None, "status": "running"}]
        self.assertEqual(state.hold_for_runs(f, state.decide(f))["next"], {"action": "wait-agent", "run": "t1"})


class ObjectionTest(unittest.TestCase):
    def objection(self, **round_over):
        return base_facts(build=[{"round": 1, "attempt": 1, "status": "objection", "objection": "plan"}],
                          rounds={1: rnd(**round_over)})

    def test_a_plan_departure_is_judged_by_the_orchestrator(self):
        self.assertEqual(nxt(self.objection()), "judge-objection")

    def test_ruling_continue_and_needs_human(self):
        self.assertEqual(nxt(self.objection(rulings={1: "CONTINUE"})), "spawn-builder")
        self.assertEqual(state.decide(self.objection(rulings={1: "NEEDS-HUMAN"}))["stage"], "needs-human")


class ApprovalAndMergeTest(unittest.TestCase):
    def approved_facts(self, **over):
        f = base_facts(build=[done(1)], rounds={1: passing()}, human_approval={"head": H1})
        f.update(over)
        return f

    def test_a_passed_job_always_waits_for_the_human(self):
        r = state.decide(base_facts(build=[done(1)], rounds={1: passing()}))
        self.assertEqual((r["stage"], r["next"].get("ready")), ("needs-human", True))
        self.assertEqual(r["reason"], state.READY)

    def wiki(self, **over):
        return {"enabled": True, "round": 1, "attempts": 0, "status": None, "before": None, "only_wiki": None,
                "stray": [], **over}

    def passed(self, **over):
        f = base_facts(build=[done(1)], rounds={1: passing()})
        f.update(over)
        return f

    def test_a_project_with_a_wiki_gets_the_wiki_pass_once_every_review_passed(self):
        r = state.decide(self.passed(wiki=self.wiki()))
        self.assertEqual((r["stage"], r["next"]["action"], r["next"]["round"], r["next"]["attempt"]),
                         ("reviewing", "spawn-wiki", 1, 1))
        self.assertTrue(state.decide(self.passed(wiki=self.wiki(enabled=False)))["next"].get("ready"))

    def test_the_developer_approves_the_head_the_wiki_pass_ended_on(self):
        f = self.passed(head=H2, wiki=self.wiki(attempts=1, status="done", before=H1, only_wiki=True))
        self.assertTrue(state.decide(f)["next"].get("ready"))
        f["human_approval"] = {"head": H2}
        self.assertEqual(nxt(f), "rebase")

    def test_the_wiki_pass_is_tried_twice_and_then_the_job_goes_on_without_it(self):
        self.assertEqual(state.decide(self.passed(wiki=self.wiki(attempts=1)))["next"]["attempt"], 2)
        self.assertTrue(state.decide(self.passed(wiki=self.wiki(attempts=2)))["next"].get("ready"))

    def test_a_wiki_pass_that_says_done_with_uncommitted_pages_is_tried_again_and_then_stops(self):
        ws = {"exists": True, "on_branch": True, "dirty": ["Wiki/a.md"], "rebase": False, "unmerged": []}
        r = state.decide(self.passed(workspace=ws, wiki=self.wiki(status="done", attempts=1)))
        self.assertEqual((r["next"]["action"], r["next"]["attempt"]), ("spawn-wiki", 2))
        r = state.decide(self.passed(workspace=ws, wiki=self.wiki(status="done", attempts=2)))
        self.assertEqual(r["stage"], "needs-human")
        self.assertFalse(r["next"].get("ready"))

    def test_a_wiki_pass_that_changed_other_files_stops_for_a_human(self):
        f = self.passed(head=H2, wiki=self.wiki(attempts=1, status="done", before=H1, only_wiki=False,
                                                stray=["src/a.ts"]))
        r = state.decide(f)
        self.assertEqual(r["stage"], "needs-human")
        self.assertFalse(r["next"].get("ready"))
        self.assertIn("src/a.ts", r["reason"])

    def test_a_rework_after_the_wiki_pass_builds_and_reviews_again_before_the_next_one(self):
        f = self.passed(head=H2, build=[done(1), done(2)], rounds={1: passing(), 2: rnd(head=H2)},
                        reworks=[{"number": 1, "start_round": 2}], wiki=self.wiki(round=2))
        self.assertEqual(nxt(f), "schedule")

    def test_approval_of_an_older_head_does_not_count(self):
        f = base_facts(head=H2, build=[done(1)], rounds={1: passing(head=H2)}, human_approval={"head": H1})
        self.assertEqual(state.decide(f)["stage"], "needs-human")

    def test_approval_leads_to_a_rebase(self):
        self.assertEqual(nxt(self.approved_facts()), "rebase")

    def test_a_clean_green_rebase_keeps_the_approval_and_opens_the_pr(self):
        f = self.approved_facts(head=H2, rebase={"history": [{"before": H1, "after": H2, "result": "green",
                                                               "target": T1, "round": 2, "fingerprint": FP}]})
        self.assertEqual(nxt(f), "pr")

    def test_a_moved_target_means_rebasing_again(self):
        f = self.approved_facts(head=H2, target_head=T2, rebase={"history": [
            {"before": H1, "after": H2, "result": "green", "target": T1, "round": 2, "fingerprint": FP}]})
        self.assertEqual(nxt(f), "rebase")

    def test_a_red_rebase_starts_a_builder_round(self):
        f = base_facts(build=[done(1)], rounds={1: passing()}, head=H2, human_approval={"head": H1},
                       rebase={"history": [{"before": H1, "after": H2, "result": "red", "target": T1, "round": 2}]})
        r = state.decide(f)
        self.assertEqual((r["next"]["action"], r["next"]["round"]), ("spawn-builder", 2))

    def test_every_rebase_conflict_goes_to_the_builder(self):
        ws = {"exists": True, "on_branch": True, "rebase": True, "dirty": [], "unmerged": ["tests/a_test.py", "src/a.py"]}
        f = self.approved_facts(workspace=ws, rebase={"history": [{"before": H1, "result": "conflict", "round": 2}]})
        self.assertEqual(nxt(f), "spawn-builder")
        f["workspace"]["unmerged"] = []
        self.assertEqual(nxt(f), "rebase-continue")

    def test_open_pr_is_waiting_and_merged_pr_finishes(self):
        f = self.approved_facts(rebase={"history": [{"before": H1, "after": H1, "result": "green", "target": T1, "fingerprint": FP}]},
                                pr={"state": "OPEN", "head": H1, "url": "u"})
        r = state.decide(f)
        self.assertEqual((r["stage"], r["waiting"]), ("pr-open", True))
        f["pr"]["state"] = "MERGED"
        self.assertEqual(nxt(f), "finish")

    def test_local_merge_mode(self):
        f = self.approved_facts(merge_mode="local-merge",
                                rebase={"history": [{"before": H1, "after": H1, "result": "green", "target": T1, "fingerprint": FP}]})
        self.assertEqual(nxt(f), "merge-local")
        f["merge"] = {"merged": False, "head": H1, "blocked": "main is checked out"}
        self.assertEqual(state.decide(f)["stage"], "needs-human")
        f["merge_retry"] = True
        self.assertEqual(nxt(f), "merge-local")

    def test_a_merge_whose_tree_differs_from_the_verified_branch_stops_for_a_human(self):
        f = self.approved_facts(merge_mode="local-merge")
        f["merge"] = {"merged": True, "commit": "c" * 40, "previous": "p" * 40, "target": "develop", "tree_matches": False}
        r = state.decide(f)
        self.assertEqual((r["stage"], r["next"]["action"]), ("needs-human", "needs-human"))
        self.assertIn("git reset --hard pppppppppppp", r["reason"])


class OpenPrTest(unittest.TestCase):
    def open_pr(self, **over):
        f = base_facts(build=[done(1)], rounds={1: passing()}, human_approval={"head": H1},
                       rebase={"history": [{"before": H1, "after": H1, "result": "green", "target": T1, "fingerprint": FP}]},
                       pr={"state": "OPEN", "head": H1, "url": "u"})
        f.update(over)
        return f

    def test_an_open_pr_on_the_current_target_waits(self):
        self.assertEqual(nxt(self.open_pr()), "wait-pr")

    def test_an_open_pr_is_rebased_when_the_target_moves(self):
        r = state.decide(self.open_pr(target_head=T2))
        self.assertEqual((r["stage"], r["next"]["action"]), ("rebasing", "rebase"))
        self.assertIn("open PR", r["reason"])


class LiveRunTest(unittest.TestCase):
    def test_a_live_builder_holds_every_step_of_its_job(self):
        f = base_facts(build=[done(1)], rounds={1: rnd(verifies={})})
        f["live_runs"] = [{"run": "r1", "role": "builder", "round": 1, "attempt": 1, "status": "running"}]
        r = state.hold_for_runs(f, state.decide(f))
        self.assertEqual(r["next"], {"action": "wait-agent", "run": "r1"})

    def test_a_cancel_is_not_held(self):
        f = base_facts(backlog="cancelled")
        f["live_runs"] = [{"run": "r1", "role": "builder", "round": 1, "attempt": 1, "status": "running"}]
        self.assertEqual(state.hold_for_runs(f, state.decide(f))["next"]["action"], "cancel")

    def test_running_reviewers_are_not_listed_again(self):
        f = base_facts(build=[done(1)], rounds={1: rnd({}, scheduled=["code", "ui"])})
        f["live_runs"] = [{"run": "r2", "role": "review-code", "round": 1, "attempt": None, "status": "reserved"}]
        self.assertEqual(state.hold_for_runs(f, state.decide(f))["next"]["reviewers"], ["ui"])
        f["live_runs"].append({"run": "r3", "role": "review-ui", "round": 1, "attempt": None, "status": "running"})
        self.assertEqual(state.hold_for_runs(f, state.decide(f))["next"]["action"], "wait-agent")


    def test_a_reviewer_still_running_holds_the_next_round_and_the_approval(self):
        running = [{"run": "r4", "role": "review-code", "round": 1, "attempt": None, "status": "running"}]
        for rounds in ({1: rnd({"code": "CHANGES"})}, {1: passing()}):
            f = base_facts(build=[done(1)], rounds=rounds)
            f["live_runs"] = running
            r = state.hold_for_runs(f, state.decide(f))
            self.assertEqual((r["next"], r["stage"], r["waiting"]),
                             ({"action": "wait-agent", "runs": ["r4"]}, "reviewing", False))


class HumanTest(unittest.TestCase):
    def test_human_approval_skips_the_open_verdicts(self):
        rounds = {n: rnd({"code": "CHANGES"}) for n in (1, 2, 3)}
        f = base_facts(build=[done(1), done(2), done(3)], rounds=rounds, human_approval={"head": H1})
        self.assertEqual(nxt(f), "rebase")

    def test_rework_starts_a_fresh_cycle_at_its_round(self):
        f = base_facts(build=[done(1)], rounds={1: passing()}, human_approval={"head": H1},
                       pr={"state": "OPEN", "head": H1}, reworks=[{"file": "rework-1.md", "number": 1, "start_round": 2}])
        r = state.decide(f)
        self.assertEqual((r["next"]["action"], r["next"]["round"]), ("spawn-builder", 2))

    def test_rework_has_a_fresh_round_budget(self):
        rounds = {n: rnd({"code": "CHANGES"}) for n in (1, 2, 3, 4)}
        f = base_facts(build=[done(n) for n in (1, 2, 3, 4)], rounds=rounds,
                       reworks=[{"file": "rework-1.md", "number": 1, "start_round": 4}])
        r = state.decide(f)
        self.assertEqual((r["next"]["action"], r["next"]["round"]), ("spawn-builder", 5))

    def test_cancelled_job_with_a_branch_is_cleaned_up(self):
        f = base_facts(backlog="cancelled")
        self.assertEqual(nxt(f), "cancel")
        f["branch_exists"] = False
        self.assertEqual(state.decide(f)["stage"], "cancelled")

    def test_an_agent_that_never_moves_the_head_is_stopped(self):
        spawns = [{"agent": "builder", "round": 1, "head": H1, "conflicts": []}] * 3
        f = base_facts(spawns=spawns, build=[{"round": 1, "attempt": 2, "status": None, "objection": None}])
        self.assertEqual(state.decide(f)["stage"], "needs-human")


class GatherTest(SandboxTest):
    def test_a_fresh_job_reads_as_starting_after_take(self):
        job = self.sb.queue_job()
        self.assertEqual(state.state(self.paths, self.cfg, job)["next"]["action"], "take")
        from fx import queue
        queue.take(self.paths)
        self.assertEqual(state.state(self.paths, self.cfg, job)["next"]["action"], "start")
        workspace.open_(self.paths, self.cfg, job)
        self.assertEqual(state.state(self.paths, self.cfg, job)["next"]["action"], "start")


if __name__ == "__main__":
    unittest.main()
