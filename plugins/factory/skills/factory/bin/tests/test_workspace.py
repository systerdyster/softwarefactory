import threading
import unittest

from support import SandboxTest, git

from fx import config, workspace
from fx.errors import FactoryError


class WorkspaceTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.other = self.sb.queue_job("002-other", "Other")

    def setup_runs(self, job_id="001-answer"):
        return (self.sb.ws_of(job_id) / "setup.log").read_text().count("setup")

    def test_open_creates_the_job_branch_from_the_target_and_runs_setup_once(self):
        result = workspace.open_(self.paths, self.cfg, self.job)
        self.assertTrue(result["created"] and result["branch_created"])
        s = workspace.check(self.paths, self.job)
        self.assertEqual((s["branch"], s["head"]), ("factory/001-answer", git(self.sb.root, "rev-parse", "origin/main")))
        self.assertEqual(self.setup_runs(), 1)
        self.assertFalse(workspace.open_(self.paths, self.cfg, self.job)["created"])
        self.assertEqual(self.setup_runs(), 1)

    def test_two_jobs_get_two_worktrees_and_their_own_dependencies(self):
        a, b = self.sb.open_job(self.job), self.sb.open_job(self.other)
        self.assertNotEqual(a, b)
        self.assertEqual((self.setup_runs(self.job), self.setup_runs(self.other)), (1, 1))
        self.sb.write("src/app.py", "changed\n", base=a)
        self.assertFalse(workspace.check(self.paths, self.job)["clean"])
        self.assertTrue(workspace.check(self.paths, self.other)["clean"])
        self.assertEqual([w["job"] for w in workspace.listing(self.paths)], [self.job, self.other])

    def test_setup_reruns_only_when_a_lockfile_changes(self):
        self.sb.open_job()
        self.assertFalse(workspace.ensure_setup(self.paths, self.cfg, self.job))
        self.sb.write("deps.lock", "v2\n", base=self.sb.ws)
        self.sb.commit("Bump", cwd=self.sb.ws)
        self.assertTrue(workspace.ensure_setup(self.paths, self.cfg, self.job))
        self.assertEqual(self.setup_runs(), 2)

    def test_run_quotes_arguments_and_hands_each_job_its_own_output_and_port(self):
        self.sb.open_job()
        result = workspace.run(self.paths, self.cfg, self.job, "echo", ["a b", "$(whoami)"])
        self.assertIn("a b $(whoami)", result["tail"])
        self.assertIn(f"logs/{self.job}/", result["log"])
        probe = workspace._shell("echo $FACTORY_JOB $FACTORY_OUTPUT $FACTORY_PORT", self.paths, self.cfg,
                                 self.sb.ws, "env", self.job)
        job, out, port = probe["tail"].split()
        self.assertEqual((job, out), (self.job, str(self.paths.output(self.job))))
        self.assertTrue(port.isdigit())

    def test_a_serial_command_never_runs_in_two_jobs_at_once(self):
        log = self.sb.tmp / "shared.log"
        self.sb.write("project.md", self.paths.project.read_text()
                      .replace("[factory]\n", "[factory]\nserial = seed\n")
                      .replace("echo = echo\n", f"echo = echo\nseed = echo start >> {log} && sleep 0.3 && echo end >> {log}\n"),
                      base=self.paths.factory)
        cfg = config.load(self.paths)
        for job in (self.job, self.other):
            self.sb.open_job(job)
        runs = [threading.Thread(target=workspace.run, args=(self.paths, cfg, job, "seed", [])) for job in (self.job, self.other)]
        for t in runs:
            t.start()
        for t in runs:
            t.join()
        self.assertEqual(log.read_text().split(), ["start", "end", "start", "end"])

    def test_unknown_command_is_refused(self):
        self.sb.open_job()
        with self.assertRaises(FactoryError):
            workspace.run(self.paths, self.cfg, self.job, "deploy", [])

    def test_verify_runs_every_check_and_stops_at_the_first_red_one(self):
        self.sb.open_job()
        green = workspace.verify(self.paths, self.cfg, self.job)
        self.assertEqual(([s["name"] for s in green["steps"]], green["green"]), (["lint", "suite"], True))
        self.sb.write("LINT_ERROR", "", base=self.sb.ws)
        self.sb.write("BROKEN", "", base=self.sb.ws)
        red = workspace.verify(self.paths, self.cfg, self.job)
        self.assertEqual((red["green"], red["failed"], len(red["steps"])), (False, "lint", 1))
        self.assertEqual(red["fingerprint"], green["fingerprint"])

    def test_remove_refuses_uncommitted_work_unless_discarded_and_spares_the_other_job(self):
        self.sb.open_job(self.job)
        self.sb.open_job(self.other)
        self.sb.write("new.txt", "x", base=self.sb.ws)
        with self.assertRaises(FactoryError):
            workspace.remove(self.paths, self.cfg, self.job)
        self.assertTrue(workspace.remove(self.paths, self.cfg, self.job, discard=True)["removed"])
        self.assertFalse(self.sb.ws.exists())
        self.assertTrue(workspace.check(self.paths, self.other)["clean"])

    def test_probe_proves_the_target_green_and_leaves_nothing_behind(self):
        self.assertTrue(workspace.probe(self.paths, self.cfg)["green"])
        self.assertEqual(workspace.listing(self.paths), [])
        self.sb.push_target_change("BROKEN", "")
        self.assertEqual(workspace.probe(self.paths, self.cfg)["failed"], "suite")


if __name__ == "__main__":
    unittest.main()
