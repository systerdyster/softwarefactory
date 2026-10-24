"""Lifecycle events: when each is due, what a handler is told, and a command run against a real job."""

import unittest

from support import SandboxTest
from test_state import base_facts

from fx import actions, config, events, pr, queue, state
from fx.errors import FactoryError


def handlers(**over):
    lines = "\n".join(f"{k.replace('_', '-')} = {v}" for k, v in over.items())
    return config.parse_text(f"```ini\n[events]\n{lines}\n```\n")


class DueTest(unittest.TestCase):
    def test_a_job_is_claimed_once_when_the_orchestrator_takes_it(self):
        self.assertEqual(events.due("queued", []), [])
        self.assertEqual(events.due("starting", []), ["claimed"])
        self.assertEqual(events.due("starting", ["claimed"]), [])

    def test_a_phase_happens_each_time_the_job_enters_it(self):
        self.assertEqual(events.due("building", ["claimed"]), ["started"])
        self.assertEqual(events.due("reviewing", ["claimed", "started"]), [])
        self.assertEqual(events.due("needs-human", ["claimed", "started"]), ["waiting"])
        self.assertEqual(events.due("building", ["claimed", "started", "waiting"]), ["started"])

    def test_a_phase_no_handler_ran_for_leaves_the_last_phase_as_it_was(self):
        self.assertEqual(events.due("building", ["claimed", "started", "failed:waiting"]), [])

    def test_a_pr_hands_the_job_off_and_its_merge_completes_it(self):
        self.assertEqual(events.due("pr-open", ["claimed", "started"]), ["handed-off"])
        self.assertEqual(events.due("merged", ["claimed", "started", "handed-off"]), ["completed"])
        self.assertEqual(events.due("done", ["claimed", "started", "handed-off", "completed"]), [])

    def test_the_factorys_own_steps_after_approval_start_nothing(self):
        self.assertEqual(events.due("rebasing", ["claimed", "started", "handed-off"]), [])
        self.assertEqual(events.due("merging", ["claimed", "started", "waiting"]), [])
        self.assertEqual(events.due("needs-human", ["claimed", "started", "waiting"]), [])

    def test_a_failed_handler_is_due_again_until_it_is_given_up(self):
        cfg = handlers(started="skill:case-status In Progress")
        f = base_facts(events_log=["claimed", "failed:started", "failed:started"])
        self.assertEqual([t["event"] for t in events.todo(cfg, f, "building")], ["started"])
        f["events_log"].append("failed:started")
        self.assertEqual((events.todo(cfg, f, "building"), events.given_up(cfg, f, "building")), ([], ["started"]))
        f["events_log"] += ["waiting", "failed:started"]
        self.assertEqual([t["event"] for t in events.todo(cfg, f, "building")], ["started"])

    def test_another_handler_running_never_revives_one_given_up(self):
        cfg = handlers(claimed="skill:case-status claim", started="skill:case-status In Progress")
        f = base_facts(events_log=["failed:claimed"] * events.MAX_FAILURES + ["started"])
        self.assertEqual((events.todo(cfg, f, "building"), events.given_up(cfg, f, "building")), ([], ["claimed"]))
        f["events_log"] = ["claimed", "failed:started", "failed:started", "failed:started", "handed-off"]
        self.assertEqual(events.given_up(cfg, f, "building"), [])

    def test_a_local_merge_hands_off_and_completes_in_one_step(self):
        self.assertEqual(events.due("merged", ["claimed", "started", "waiting", "started"]), ["handed-off", "completed"])

    def test_a_cancelled_job_is_cancelled_once_and_never_claimed(self):
        self.assertEqual(events.due("cancelled", []), ["cancelled"])
        self.assertEqual(events.due("cancelled", ["claimed", "started", "cancelled"]), [])


class TodoTest(unittest.TestCase):
    def test_an_event_without_a_handler_is_skipped(self):
        cfg = handlers(started="skill:case-status In Progress")
        self.assertEqual([t["event"] for t in events.todo(cfg, base_facts(), "building")], ["started"])

    def test_a_skill_is_told_the_job_case_and_branch_in_its_arguments(self):
        cfg = handlers(started="skill:case-status In Progress")
        f = base_facts(meta={"title": "T", "case": "OPT-1", "branch": "feature/OPT-1-t", "developer": "Ann Berg"},
                       events_log=["claimed"])
        item = events.todo(cfg, f, "building")[0]
        self.assertEqual((item["kind"], item["skill"]), ("skill", "case-status"))
        self.assertEqual(item["skill_args"],
                         'In Progress job=OPT-1 case=OPT-1 branch=feature/OPT-1-t developer="Ann Berg"')

    def test_the_first_hand_off_carries_the_test_note_when_there_is_one(self):
        cfg = handlers(handed_off="skill:case-status In Review")
        f = base_facts(events_log=["claimed", "started"], test_note="Open the catalogue.",
                       pr={"url": "https://pr/1"})
        context = events.todo(cfg, f, "pr-open")[0]["context"]
        self.assertEqual((context["test_note"], context["pr"]), ("/jobs/OPT-1/test-note.md", "https://pr/1"))
        f["events_log"] += ["handed-off", "started"]
        self.assertNotIn("test_note", events.todo(cfg, f, "pr-open")[0]["context"])
        f.update(events_log=["claimed", "started"], test_note="")
        self.assertNotIn("test_note", events.todo(cfg, f, "pr-open")[0]["context"])

    def test_a_handler_is_a_command_or_a_named_skill(self):
        self.assertEqual(handlers(completed="./notify.sh done").event_handler("completed"),
                         {"kind": "command", "command": "./notify.sh done"})
        self.assertIsNone(handlers().event_handler("waiting"))
        with self.assertRaises(FactoryError):
            handlers(completed="skill:").event_handler("completed")
        with self.assertRaises(FactoryError):
            handlers().event_handler("merged")


class RunTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        queue.take(self.paths)

    def project(self, handler):
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "[events]\n", f"[events]\nclaimed = {handler}\n"), encoding="utf-8")
        self.cfg = config.load(self.paths)

    def current(self):
        return state.state(self.paths, self.cfg, self.job)

    def test_a_command_runs_with_the_context_and_is_logged_once(self):
        self.project('printf "%s %s" "$FACTORY_EVENT" "$FACTORY_JOB" > claimed.txt')
        self.assertEqual(events.run(self.paths, self.cfg, self.job, "claimed", self.current())["ran"], "claimed")
        self.assertEqual((self.paths.root / "claimed.txt").read_text(), f"claimed {self.job}")
        self.assertEqual(self.current()["events_todo"], [])
        with self.assertRaises(FactoryError):
            events.run(self.paths, self.cfg, self.job, "claimed", self.current())

    def test_a_failed_command_is_logged_as_failed_and_given_up_after_three(self):
        self.project("echo no such status; exit 4")
        for _ in range(events.MAX_FAILURES):
            self.assertEqual([t["event"] for t in self.current()["events_todo"]], ["claimed"])
            with self.assertRaises(FactoryError):
                events.run(self.paths, self.cfg, self.job, "claimed", self.current())
        current = self.current()
        self.assertEqual((current["events_todo"], current["events_given_up"]), ([], ["claimed"]))
        self.assertIn("failed:claimed exited 4: no such status", (self.paths.job(self.job) / "events.md").read_text())

    def test_a_cancelled_jobs_failed_handler_keeps_the_run_going_until_given_up(self):
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "[events]\n", "[events]\ncancelled = exit 3\n"), encoding="utf-8")
        self.cfg = config.load(self.paths)
        self.sb.queue_job("002-before-events", "Finished before events existed")
        queue.mark(self.paths, "002-before-events", "cancelled")
        lease = actions.acquire_lease(self.paths)["lease"]
        actions.cancel(self.paths, self.cfg, self.job, "not needed", pr)
        for _ in range(events.MAX_FAILURES):
            result = actions.next_job(self.paths, self.cfg, lease)
            self.assertEqual((result["action"], result["events"]), ("work", [self.job]))
            with self.assertRaises(FactoryError):
                events.run(self.paths, self.cfg, self.job, "cancelled", self.current())
        self.assertEqual(actions.next_job(self.paths, self.cfg, lease)["action"], "idle")

    def test_a_skill_is_the_sessions_to_run(self):
        self.project("skill:case-status claim")
        with self.assertRaises(FactoryError):
            events.run(self.paths, self.cfg, self.job, "claimed", self.current())
        events.log(self.paths, self.job, "claimed")
        self.assertEqual(self.current()["events_todo"], [])


if __name__ == "__main__":
    unittest.main()
