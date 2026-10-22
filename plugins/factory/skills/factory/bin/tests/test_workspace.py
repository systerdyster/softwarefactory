import unittest

from support import SandboxTest, git

from fx import workspace
from fx.errors import FactoryError


class WorkspaceTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.create_workspace()
        self.job = self.sb.queue_job()

    def setup_runs(self):
        return (self.sb.ws / "setup.log").read_text().count("setup")

    def test_create_is_detached_at_the_target_and_runs_setup_once(self):
        s = workspace.check(self.paths)
        self.assertIsNone(s["branch"])
        self.assertEqual(s["head"], git(self.sb.root, "rev-parse", "origin/main"))
        self.assertEqual(self.setup_runs(), 1)
        self.assertFalse(workspace.create(self.paths, self.cfg)["created"])

    def test_switch_creates_the_job_branch_from_the_target(self):
        result = workspace.switch(self.paths, self.cfg, self.job)
        self.assertTrue(result["created"])
        self.assertEqual(workspace.check(self.paths)["branch"], "factory/001-answer")

    def test_switch_refuses_a_dirty_workspace(self):
        workspace.switch(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "changed\n", base=self.sb.ws)
        with self.assertRaises(FactoryError):
            workspace.switch(self.paths, self.cfg, "002-other")

    def test_setup_reruns_only_when_a_lockfile_differs(self):
        workspace.switch(self.paths, self.cfg, self.job)
        self.assertEqual(self.setup_runs(), 1)
        self.sb.write("deps.lock", "v2\n", base=self.sb.ws)
        self.sb.commit("Bump", cwd=self.sb.ws)
        workspace.detach(self.paths, self.cfg)
        self.assertEqual(self.setup_runs(), 1)
        workspace.switch(self.paths, self.cfg, self.job)
        self.assertEqual(self.setup_runs(), 2)
        workspace.detach(self.paths, self.cfg)
        self.assertEqual(self.setup_runs(), 3)

    def test_run_quotes_arguments_into_the_target_placeholder(self):
        result = workspace.run(self.paths, self.cfg, "echo", ["a b", "$(whoami)"])
        self.assertIn("a b $(whoami)", result["tail"])

    def test_unknown_command_is_refused(self):
        with self.assertRaises(FactoryError):
            workspace.run(self.paths, self.cfg, "deploy", [])

    def test_suite_reports_red_and_green(self):
        self.assertTrue(workspace.suite(self.paths, self.cfg)["green"])
        self.sb.write("BROKEN", "", base=self.sb.ws)
        self.assertFalse(workspace.suite(self.paths, self.cfg)["green"])

    def test_verify_runs_every_check_and_stops_at_the_first_red_one(self):
        green = workspace.verify(self.paths, self.cfg)
        self.assertEqual(([s["name"] for s in green["steps"]], green["green"]), (["lint", "suite"], True))
        self.sb.write("LINT_ERROR", "", base=self.sb.ws)
        self.sb.write("BROKEN", "", base=self.sb.ws)
        red = workspace.verify(self.paths, self.cfg)
        self.assertEqual((red["green"], red["failed"], len(red["steps"])), (False, "lint", 1))
        self.assertEqual(red["fingerprint"], green["fingerprint"])

    def test_detach_with_discard_throws_away_uncommitted_work(self):
        workspace.switch(self.paths, self.cfg, self.job)
        self.sb.write("new.txt", "x", base=self.sb.ws)
        workspace.detach(self.paths, self.cfg, discard=True)
        self.assertTrue(workspace.check(self.paths)["clean"])
        self.assertFalse((self.sb.ws / "new.txt").exists())


if __name__ == "__main__":
    unittest.main()
