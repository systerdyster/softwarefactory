"""Two jobs at once: each in its own worktree, at most two active, waiting jobs out of the way, one
orchestrator, and finishing or cancelling one job leaving the other alone."""

import unittest

from support import SandboxTest, git

from fx import actions, config, pr, queue, runs, schedule, state, workspace
from fx.errors import FactoryError


class ParallelTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.a = self.sb.queue_job("001-answer", "Answer")
        self.b = self.sb.queue_job("002-greeting", "Greeting")
        self.c = self.sb.queue_job("003-third", "Third")
        self.lease = actions.acquire_lease(self.paths)["lease"]

    def next(self):
        return actions.next_job(self.paths, self.cfg, self.lease)

    def build(self, job, rel, text):
        ws = self.sb.ws_of(job)
        self.sb.write(rel, text, base=ws)
        self.sb.commit(f"Change {rel}", cwd=ws)
        self.sb.write("build.md", "## Round 1\nStatus: done\n", base=self.paths.job(job))

    def to_ready(self, job, rel, text):
        """Builds, verifies and passes review: the job waits for its human approval."""
        self.build(job, rel, text)
        actions.verify(self.paths, self.cfg, job, 1, 1)
        schedule.schedule(self.paths, self.cfg, job, 1, 1)
        self.sb.write("round-1/review-code.md", "VERDICT: PASS\n\n## Findings\n", base=self.paths.job(job))
        s = state.state(self.paths, self.cfg, job)
        self.assertTrue(s["next"].get("ready"), s)
        return s

    def test_two_jobs_build_at_once_in_their_own_worktrees(self):
        first = self.next()
        self.assertEqual((first["jobs"], first["taken"]), ([self.a, self.b], [self.a, self.b]))
        for job in (self.a, self.b):
            actions.start(self.paths, self.cfg, job)
        self.build(self.a, "src/app.py", "def answer():\n    return 42\n")
        self.sb.write("scratch.txt", "work in progress", base=self.sb.ws_of(self.b))
        self.assertTrue(actions.verify(self.paths, self.cfg, self.a, 1, 1)["green"])
        self.assertEqual(workspace.check(self.paths, self.b)["dirty"], ["scratch.txt"])
        self.assertNotIn("src/app.py", git(self.sb.ws_of(self.b), "diff", "--name-only", "origin/main"))
        self.assertEqual(self.next()["jobs"], [self.a, self.b])
        self.assertEqual(queue.entry(self.paths, self.c)["status"], "queued")

    def test_parallel_sets_how_many_jobs_take_a_slot(self):
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "max_diff = 400\n", "max_diff = 400\nparallel = 3\n"), encoding="utf-8")
        self.cfg = config.load(self.paths)
        self.sb.queue_job("004-fourth", "Fourth")
        result = self.next()
        self.assertEqual((result["jobs"], result["slots"]), ([self.a, self.b, self.c], 3))
        for job in result["jobs"]:
            actions.start(self.paths, self.cfg, job)
        self.assertEqual(len({workspace.job_port(self.paths, job) for job in result["jobs"]}), 3)

    def test_a_job_waiting_for_approval_keeps_its_worktree_but_frees_its_slot(self):
        self.next()
        for job in (self.a, self.b):
            actions.start(self.paths, self.cfg, job)
        self.to_ready(self.a, "src/app.py", "def answer():\n    return 42\n")
        result = self.next()
        self.assertEqual((result["jobs"], result["taken"]), ([self.b, self.c], [self.c]))
        self.assertEqual([w["id"] for w in result["waiting"]], [self.a])
        self.assertTrue(self.sb.ws_of(self.a).exists())

    def test_the_run_waits_for_a_human_and_ends_only_when_every_job_is_finished(self):
        queue.mark(self.paths, self.b, "cancelled")
        queue.mark(self.paths, self.c, "cancelled")
        self.next()
        actions.start(self.paths, self.cfg, self.a)
        self.to_ready(self.a, "src/app.py", "def answer():\n    return 42\n")
        self.assertEqual(self.next()["action"], "wait")
        self.assertEqual(actions.wait(self.paths, timeout=0), {"inbox": 0, "timed_out": True})
        actions.write_command(self.paths, "approve", self.a, "", "dev")
        self.assertEqual(actions.wait(self.paths, timeout=60), {"inbox": 1, "timed_out": False})
        queue.mark(self.paths, self.a, "done")
        self.assertEqual(self.next()["action"], "idle")

    def test_a_rework_waits_for_a_free_slot(self):
        self.next()
        for job in (self.a, self.b):
            actions.start(self.paths, self.cfg, job)
        self.to_ready(self.a, "src/app.py", "def answer():\n    return 42\n")
        self.next()
        actions.start(self.paths, self.cfg, self.c)
        actions.rework(self.paths, self.cfg, self.a, "use 41 after all", "dev", pr)
        result = self.next()
        self.assertEqual((result["jobs"], result["waiting_for_slot"]), ([self.b, self.c], [self.a]))

    def test_a_restart_neither_takes_nor_starts_twice(self):
        self.next()
        actions.start(self.paths, self.cfg, self.a)
        self.sb.write("work.txt", "kept", base=self.sb.ws_of(self.a))
        with self.assertRaises(FactoryError):
            actions.acquire_lease(self.paths)
        with self.assertRaises(FactoryError):
            actions.acquire_lease(self.paths, takeover=True)
        self.lease = actions.acquire_lease(self.paths, takeover=True, force=True)["lease"]
        again = self.next()
        self.assertEqual((again["jobs"], again["taken"]), ([self.a, self.b], []))
        self.assertFalse(workspace.open_(self.paths, self.cfg, self.a)["created"])
        self.assertTrue((self.sb.ws_of(self.a) / "work.txt").exists())

    def test_an_old_lease_no_longer_takes_jobs(self):
        stale = self.lease
        self.lease = actions.acquire_lease(self.paths, takeover=True, force=True)["lease"]
        with self.assertRaises(FactoryError):
            actions.next_job(self.paths, self.cfg, stale)

    def test_a_job_queued_after_another_waits_until_it_is_done(self):
        d = self.sb.queue_job("004-depends", "Depends", after=[self.a])
        queue.mark(self.paths, self.b, "cancelled")
        queue.mark(self.paths, self.c, "cancelled")
        result = self.next()
        self.assertEqual((result["jobs"], result["blocked"]), ([self.a], [{"id": d, "after": [self.a]}]))
        queue.mark(self.paths, self.a, "done")
        self.assertEqual(self.next()["taken"], [d])

    def test_finishing_or_cancelling_one_job_leaves_the_other_alone(self):
        self.next()
        for job in (self.a, self.b):
            actions.start(self.paths, self.cfg, job)
        self.sb.write("scratch.txt", "b's work", base=self.sb.ws_of(self.b))
        for job, agent in ((self.a, "agent-a"), (self.b, "agent-b")):
            runs.attach(self.paths, runs.reserve(self.paths, self.cfg, job, "builder", 1, 1, None)["run"], agent)
        actions.cancel(self.paths, self.cfg, self.a, "not needed", pr)
        self.assertFalse(self.sb.ws_of(self.a).exists())
        self.assertEqual(workspace.check(self.paths, self.b)["dirty"], ["scratch.txt"])
        self.assertEqual(runs.for_agent(self.paths, "agent-a")["status"], "aborted")
        self.assertEqual(runs.for_agent(self.paths, "agent-b")["status"], "running")
        self.assertEqual(state.state(self.paths, self.cfg, self.b)["next"]["action"], "wait-agent")


class MovedTargetTest(SandboxTest):
    def test_merging_one_job_makes_the_approved_other_rebase_and_verify_again(self):
        a, b = self.sb.queue_job("001-answer", "Answer"), self.sb.queue_job("002-greeting", "Greeting")
        lease = actions.acquire_lease(self.paths)["lease"]
        actions.next_job(self.paths, self.cfg, lease)
        helper = ParallelTest("test_two_jobs_build_at_once_in_their_own_worktrees")
        helper.sb, helper.paths, helper.cfg = self.sb, self.paths, self.cfg
        for job in (a, b):
            actions.start(self.paths, self.cfg, job)
        helper.to_ready(b, "src/greeting.py", "HELLO = 'hej'\n")
        actions.approve(self.paths, self.cfg, b, "Fredrik")
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "rebase")
        self.assertEqual(actions.rebase(self.paths, self.cfg, b)["result"], "green")
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "pr")

        helper.build(a, "src/app.py", "def answer():\n    return 42\n")
        git(self.sb.root, "push", "origin", f"{git(self.sb.root, 'rev-parse', 'factory/001-answer')}:refs/heads/main")
        git(self.sb.root, "fetch", "origin")
        s = state.state(self.paths, self.cfg, b)
        self.assertEqual(s["next"]["action"], "rebase")
        entry = actions.rebase(self.paths, self.cfg, b)
        self.assertEqual((entry["result"], entry.get("reused")), ("green", None))
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "pr")

    def test_an_open_pr_is_rebased_checked_and_updated_when_the_other_job_lands(self):
        a, b = self.sb.queue_job("001-answer", "Answer"), self.sb.queue_job("002-greeting", "Greeting")
        lease = actions.acquire_lease(self.paths)["lease"]
        actions.next_job(self.paths, self.cfg, lease)
        helper = ParallelTest("test_two_jobs_build_at_once_in_their_own_worktrees")
        helper.sb, helper.paths, helper.cfg = self.sb, self.paths, self.cfg
        for job in (a, b):
            actions.start(self.paths, self.cfg, job)
        helper.to_ready(b, "src/greeting.py", "HELLO = 'hej'\n")
        actions.approve(self.paths, self.cfg, b, "Fredrik")
        actions.rebase(self.paths, self.cfg, b)
        self.assertEqual(pr.open_or_update(self.paths, self.cfg, b)["state"], "MANUAL")
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "wait-pr")
        logs = set(self.paths.logs.rglob("*.log"))
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "wait-pr")
        self.assertEqual(set(self.paths.logs.rglob("*.log")), logs)

        helper.build(a, "src/app.py", "def answer():\n    return 42\n")
        git(self.sb.root, "push", "origin", f"{git(self.sb.root, 'rev-parse', 'factory/001-answer')}:refs/heads/main")
        pr.status(self.paths, self.cfg, b)
        s = state.state(self.paths, self.cfg, b)
        self.assertEqual((s["stage"], s["next"]["action"]), ("rebasing", "rebase"))
        entry = actions.rebase(self.paths, self.cfg, b)
        self.assertEqual((entry["result"], entry.get("reused")), ("green", None))
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "pr")
        record = pr.open_or_update(self.paths, self.cfg, b)
        self.assertEqual(record["head"], git(self.sb.root, "rev-parse", "factory/002-greeting"))
        self.assertEqual(state.state(self.paths, self.cfg, b)["next"]["action"], "wait-pr")

    def test_a_conflict_resolved_on_rebase_is_reviewed_again_not_covered_by_the_old_approval(self):
        b = self.sb.queue_job("002-greeting", "Greeting")
        lease = actions.acquire_lease(self.paths)["lease"]
        actions.next_job(self.paths, self.cfg, lease)
        helper = ParallelTest("test_two_jobs_build_at_once_in_their_own_worktrees")
        helper.sb, helper.paths, helper.cfg = self.sb, self.paths, self.cfg
        actions.start(self.paths, self.cfg, b)
        helper.to_ready(b, "src/app.py", "def answer():\n    return 43\n")
        actions.approve(self.paths, self.cfg, b, "Fredrik")
        self.sb.push_target_change("src/app.py", "def answer():\n    return 40\n")
        self.assertEqual(actions.rebase(self.paths, self.cfg, b)["result"], "conflict")
        ws = self.sb.ws_of(b)
        self.sb.write("src/app.py", "def answer():\n    return 43\n", base=ws)
        git(ws, "add", "src/app.py")
        self.assertEqual(actions.rebase(self.paths, self.cfg, b, continue_=True)["result"], "resolved")
        s = state.state(self.paths, self.cfg, b)
        self.assertEqual((s["stage"], s["next"]["action"]), ("building", "spawn-builder"))


if __name__ == "__main__":
    unittest.main()
