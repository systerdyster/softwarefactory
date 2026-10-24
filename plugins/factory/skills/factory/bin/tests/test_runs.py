"""Agent runs: the same work is never started twice, a restart finds the agents still working, and an
abort between reserving and starting an agent does not leave the job stuck."""

import threading
import unittest

from support import SandboxTest, agent_starts, agent_stops

from fx import actions, brief, jobs, runs, schedule, state
from fx.errors import FactoryError


class RunsTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.lease = actions.acquire_lease(self.paths)["lease"]
        actions.next_job(self.paths, self.cfg, self.lease)
        actions.start(self.paths, self.cfg, self.job)

    def action(self):
        return state.state(self.paths, self.cfg, self.job)["next"]

    def age(self, run_id, field):
        f = self.paths.factory / "runs" / f"{run_id}.json"
        jobs.write_json(f, {**jobs.read_json(f), field: "2020-01-01T00:00:00+00:00"})

    def test_a_builder_brief_reserves_the_work_and_a_second_is_refused(self):
        first = brief.brief(self.paths, self.cfg, self.job, "builder")
        run_id = runs.run_id_in(first)
        self.assertEqual(self.action(), {"action": "wait-agent", "run": run_id})
        with self.assertRaises(FactoryError):
            brief.brief(self.paths, self.cfg, self.job, "builder")

    def test_two_briefs_at_the_same_moment_reserve_one_builder(self):
        results = []

        def ask():
            try:
                results.append(runs.reserve(self.paths, self.cfg, self.job, "builder", 1, 1, None)["run"])
            except FactoryError:
                results.append(None)
        threads = [threading.Thread(target=ask) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r), 1)

    def test_a_restart_finds_the_running_builder_and_does_not_start_another(self):
        text = brief.brief(self.paths, self.cfg, self.job, "builder")
        self.assertEqual(agent_starts(self.paths.root, text, "agent-1"), 0)
        self.lease = actions.acquire_lease(self.paths, takeover=True, force=True)["lease"]
        self.assertEqual(actions.next_job(self.paths, self.cfg, self.lease)["jobs"], [self.job])
        self.assertEqual(self.action()["action"], "wait-agent")
        with self.assertRaises(FactoryError):
            brief.brief(self.paths, self.cfg, self.job, "builder")
        self.assertEqual(agent_stops(self.paths.root, "agent-1"), 0)
        self.assertEqual(self.action()["action"], "spawn-builder")

    def test_an_abort_between_the_brief_and_the_agent_expires_the_reservation(self):
        run_id = runs.run_id_in(brief.brief(self.paths, self.cfg, self.job, "builder"))
        self.assertEqual(self.action()["action"], "wait-agent")
        self.age(run_id, "created_at")
        self.assertEqual(self.action()["action"], "spawn-builder")
        second = runs.run_id_in(brief.brief(self.paths, self.cfg, self.job, "builder"))
        self.assertNotEqual(second, run_id)
        self.assertEqual(jobs.read_json(self.paths.factory / "runs" / f"{run_id}.json")["status"], "aborted")
        self.assertNotEqual(agent_starts(self.paths.root, f"Factory run: {run_id}", "late-agent"), 0)

    def test_a_running_agent_that_went_silent_can_be_replaced(self):
        text = brief.brief(self.paths, self.cfg, self.job, "builder")
        agent_starts(self.paths.root, text, "agent-1")
        self.age(runs.run_id_in(text), "seen_at")
        self.assertEqual(self.action()["action"], "spawn-builder")

    def test_a_reviewer_is_not_started_twice_either(self):
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        self.sb.write("build.md", "## Round 1\nStatus: done\n", base=self.paths.job(self.job))
        actions.verify(self.paths, self.cfg, self.job, 1, 1)
        schedule.schedule(self.paths, self.cfg, self.job, 1, 1)
        brief.brief(self.paths, self.cfg, self.job, "reviewer", "code")
        self.assertEqual(self.action()["action"], "wait-agent")
        with self.assertRaises(FactoryError):
            brief.brief(self.paths, self.cfg, self.job, "reviewer", "code")

    def test_cancelling_a_job_aborts_its_runs(self):
        text = brief.brief(self.paths, self.cfg, self.job, "builder")
        agent_starts(self.paths.root, text, "agent-1")
        from fx import pr
        actions.cancel(self.paths, self.cfg, self.job, "not needed", pr)
        self.assertEqual(runs.for_agent(self.paths, "agent-1")["status"], "aborted")

    def test_runs_that_ended_long_ago_are_pruned_and_live_or_fresh_ones_kept(self):
        old = runs.reserve(self.paths, self.cfg, self.job, "review-code", 1, None, None)["run"]
        runs.end(self.paths, old, status="done")
        self.age(old, "ended_at")
        fresh = runs.reserve(self.paths, self.cfg, self.job, "review-security", 1, None, None)["run"]
        runs.end(self.paths, fresh, status="done")
        live = runs.reserve(self.paths, self.cfg, self.job, "builder", 1, 1, None)["run"]
        self.assertEqual(runs.prune(self.paths), [old])
        self.assertEqual(sorted(r["run"] for r in runs.all_runs(self.paths)), sorted([fresh, live]))

    def test_a_recorded_binding_failure_stops_the_factory_with_its_reason(self):
        runs.record_binding_failure(self.paths, "agent_id x matches no factory run")
        result = actions.next_job(self.paths, self.cfg, self.lease)
        self.assertEqual((result["action"], result["jobs"]), ("stop", []))
        self.assertIn("matches no factory run", result["reason"])


if __name__ == "__main__":
    unittest.main()
