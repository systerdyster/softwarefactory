import unittest

from support import SandboxTest, git

from fx import actions, jobs, pr, queue, state, workspace
from fx.errors import FactoryError


class ActionsTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        queue.take(self.paths)

    def started_with_tests(self):
        actions.start(self.paths, self.cfg, self.job)
        self.sb.write("tests/test_answer.py", "assert 1\n", base=self.sb.ws)
        self.sb.commit("Tests", cwd=self.sb.ws)
        self.sb.write("tests.md", "| File |\n|---|\n| `tests/test_answer.py` |\n", base=self.paths.job(self.job))

    def test_start_records_a_green_baseline(self):
        result = actions.start(self.paths, self.cfg, self.job)
        self.assertTrue(result["baseline"]["green"])
        self.assertTrue((self.paths.job(self.job) / "baseline.json").exists())

    def test_start_on_a_red_target_refuses(self):
        self.sb.push_target_change("BROKEN", "")
        with self.assertRaises(FactoryError):
            actions.start(self.paths, self.cfg, self.job)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["stage"], "needs-human")

    def test_a_retried_start_begins_from_the_moved_target(self):
        self.sb.push_target_change("BROKEN", "")
        with self.assertRaises(FactoryError):
            actions.start(self.paths, self.cfg, self.job)
        other = self.sb.tmp / "fixer"
        git(self.sb.tmp, "clone", str(self.sb.origin), str(other))
        git(other, "rm", "-q", "BROKEN")
        git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Fix the suite")
        git(other, "push", "-q", "origin", "main")
        git(self.sb.root, "fetch", "origin")
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "start")
        self.assertTrue(actions.start(self.paths, self.cfg, self.job)["baseline"]["green"])

    def test_a_conflict_in_a_test_file_goes_to_the_builder(self):
        self.started_with_tests()
        self.sb.push_target_change("tests/test_answer.py", "assert 2\n")
        self.assertEqual(actions.rebase(self.paths, self.cfg, self.job)["result"], "conflict")
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "spawn-builder")

    def test_a_rebase_that_moves_nothing_reuses_the_verified_result(self):
        self.started_with_tests()
        self.sb.write("build.md", "## Round 1\nStatus: done\n", base=self.paths.job(self.job))
        verified = actions.verify(self.paths, self.cfg, self.job, 1, 1)
        logs_before = set(self.paths.logs.rglob("*.log"))
        entry = actions.rebase(self.paths, self.cfg, self.job)
        self.assertEqual((entry["result"], entry["after"]), ("green", verified["head"]))
        self.assertIn("round-1/verify-1.json", entry["reused"])
        self.assertEqual(set(self.paths.logs.rglob("*.log")), logs_before)

    def test_a_rebase_that_moves_nothing_still_checks_when_nothing_was_verified(self):
        self.started_with_tests()
        entry = actions.rebase(self.paths, self.cfg, self.job)
        self.assertEqual((entry["result"], entry.get("reused")), ("green", None))

    def test_rebase_onto_an_unrelated_change_is_green(self):
        self.started_with_tests()
        before = git(self.sb.ws, "rev-parse", "HEAD")
        self.sb.push_target_change("README.md", "upstream\n")
        entry = actions.rebase(self.paths, self.cfg, self.job)
        self.assertEqual(entry["result"], "green")
        self.assertEqual(entry["before"], before)
        self.assertNotEqual(entry["after"], before)

    def test_rebase_conflict_is_left_in_progress_and_continue_finishes_it(self):
        self.started_with_tests()
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        self.sb.push_target_change("src/app.py", "def answer():\n    return 40\n")
        result = actions.rebase(self.paths, self.cfg, self.job)
        self.assertEqual(result["result"], "conflict")
        self.assertEqual(result["conflicts"], ["src/app.py"])
        s = state.state(self.paths, self.cfg, self.job)
        self.assertEqual(s["stage"], "rebasing")
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        git(self.sb.ws, "add", "src/app.py")
        entry = actions.rebase(self.paths, self.cfg, self.job, continue_=True)
        self.assertEqual(entry["result"], "resolved")
        self.assertFalse(workspace.check(self.paths, self.job)["rebase_in_progress"])

    def test_cleanup_removes_the_worktree_and_both_branches(self):
        self.started_with_tests()
        git(self.sb.root, "push", "origin", "factory/001-answer")
        result = actions.cleanup(self.paths, self.cfg, self.job)
        self.assertTrue(result["local_deleted"] and result["remote_deleted"] and result["workspace"]["removed"])
        self.assertFalse(self.sb.ws.exists())

    def test_cancel_marks_the_backlog_and_writes_the_reason(self):
        self.started_with_tests()
        self.sb.write("dirty.txt", "x", base=self.sb.ws)
        actions.cancel(self.paths, self.cfg, self.job, "duplicate of OPT-2", pr)
        self.assertEqual(queue.entry(self.paths, self.job)["status"], "cancelled")
        decision = (self.paths.job(self.job) / "decision.md").read_text()
        self.assertTrue(decision.startswith("CANCELLED"))
        self.assertIn("duplicate of OPT-2", decision)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["stage"], "cancelled")

    def test_approve_is_only_for_needs_human_jobs(self):
        self.started_with_tests()
        with self.assertRaises(FactoryError):
            actions.approve(self.paths, self.cfg, self.job, "dev")

    def test_rework_needs_a_note_and_a_waiting_job(self):
        self.started_with_tests()
        with self.assertRaises(FactoryError):
            actions.rework(self.paths, self.cfg, self.job, "change it", "dev", pr)

    def test_inbox_round_trip(self):
        queued = actions.write_command(self.paths, "rework", self.job, "use the other API", "dev")
        pending = actions.inbox_list(self.paths)
        self.assertEqual(pending[0]["command"], "rework")
        actions.inbox_done(self.paths, queued["queued"], "done")
        self.assertEqual(actions.inbox_list(self.paths), [])
        self.assertTrue((self.paths.inbox_done / queued["queued"]).exists())

    def test_stop_flag_stops_only_when_nothing_is_active(self):
        lease = actions.acquire_lease(self.paths)["lease"]
        actions.stop(self.paths)
        queue.mark(self.paths, self.job, "done")
        self.sb.queue_job("002-next", "Next")
        self.assertEqual(actions.next_job(self.paths, self.cfg, lease)["action"], "stop")


if __name__ == "__main__":
    unittest.main()
