"""pipeline() turns one job's state into the steps the dashboard draws, so each moment in a job's
life is tested by stating its state and reading the steps."""

import unittest

from fx import board

T0 = "2026-10-22T10:00:00+00:00"


def job(stage, action="none", round_number=1, runs=None, verdicts=None, tidy=None, reason="", **nxt):
    return {"stage": stage, "next": {"action": action, **nxt}, "round": round_number, "runs": runs or [],
            "verdicts": verdicts or {}, "tidy": tidy, "reason": reason}


def steps(s):
    return {st["key"]: st for st in board.pipeline(s)}


def statuses(s):
    return [st["status"] for st in board.pipeline(s)]


KEPT = {"before": "a", "status": "done", "discarded": None}


class PipelineTest(unittest.TestCase):
    def test_a_queued_job_has_not_started(self):
        self.assertEqual(set(statuses(job("queued", "take", 0))), {"pending"})

    def test_the_running_builder_is_the_current_step_with_its_time(self):
        s = steps(job("building", "wait-agent", runs=[{"role": "builder", "round": 1, "status": "running", "since": T0}]))
        self.assertEqual((s["start"]["status"], s["build"]["status"], s["verify"]["status"]), ("done", "active", "pending"))
        self.assertEqual(s["build"]["since"], T0)

    def test_a_second_attempt_and_a_fix_round_say_so(self):
        self.assertEqual(steps(job("building", "spawn-builder", attempt=2))["build"]["detail"], "attempt 2")
        self.assertEqual(steps(job("building", "spawn-builder", 2, attempt=1, tidy=KEPT))["build"]["detail"], "fix round 2")

    def test_verify_and_the_tidy_pass_are_their_own_steps(self):
        self.assertEqual(statuses(job("building", "verify", attempt=1))[:4], ["done", "done", "active", "pending"])
        for s in (job("building", "spawn-tidy"), job("building", "verify", attempt="tidy", tidy=KEPT),
                  job("building", "wait-agent", runs=[{"role": "tidy", "round": 1, "status": "running", "since": T0}])):
            self.assertEqual(steps(s)["tidy"]["status"], "active")

    def test_review_shows_each_reviewer_running_waiting_or_done(self):
        s = job("reviewing", "wait-agent", tidy=KEPT, verdicts={"1": {"code": None, "ui": "PASS", "security": None}},
                runs=[{"role": "review-code", "round": 1, "status": "running", "since": T0}])
        review = steps(s)["review"]
        self.assertEqual(review["status"], "active")
        self.assertEqual([(r["name"], r["status"]) for r in review["reviewers"]],
                         [("code", "running"), ("security", "waiting"), ("ui", "PASS")])
        self.assertEqual(steps(s)["tidy"]["status"], "done")

    def test_a_tidy_pass_that_never_ran_or_was_discarded_reads_as_skipped(self):
        self.assertEqual(steps(job("reviewing", "schedule"))["tidy"]["status"], "skipped")
        tidy = steps(job("reviewing", "schedule", tidy={**KEPT, "discarded": "suite is red"}))["tidy"]
        self.assertEqual((tidy["status"], tidy["detail"]), ("skipped", "discarded: suite is red"))

    def test_a_job_ready_for_approval_waits_for_you(self):
        s = steps(job("needs-human", "needs-human", tidy=KEPT, ready=True, verdicts={"1": {"code": "PASS"}}))
        self.assertEqual((s["review"]["status"], s["approve"]["status"], s["approve"]["detail"]), ("done", "waiting", "waiting for you"))

    def test_a_job_stopped_for_a_human_is_blocked_where_it_stopped(self):
        self.assertEqual(steps(job("needs-human", "needs-human", verdicts={"1": {"code": "CHANGES"}}))["review"]["status"], "blocked")
        self.assertEqual(steps(job("needs-human", "needs-human", reason="builder made 3 attempts"))["build"]["status"], "blocked")
        self.assertEqual(steps(job("needs-human", "needs-human", 0, reason="suite is red on the target branch"))["start"]["status"],
                         "blocked")

    def test_after_approval_the_pr_step_carries_rebase_and_merge(self):
        s = steps(job("rebasing", "rebase", tidy=KEPT))
        self.assertEqual((s["approve"]["status"], s["pr"]["status"], s["pr"]["detail"]), ("done", "active", "rebasing"))
        self.assertEqual(steps(job("pr-open", "wait-pr", tidy=KEPT))["pr"]["status"], "waiting")

    def test_a_rebase_conflict_goes_back_to_the_builder(self):
        self.assertEqual(steps(job("rebasing", "spawn-builder", 2, attempt=1, tidy=KEPT))["build"]["status"], "active")

    def test_a_merged_job_is_done_throughout(self):
        self.assertEqual(statuses(job("done", tidy=KEPT)), ["done"] * 7)


if __name__ == "__main__":
    unittest.main()
