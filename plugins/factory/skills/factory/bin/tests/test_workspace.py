import socket
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

    def test_a_changed_setup_changes_the_verify_fingerprint_for_the_same_commands(self):
        before = workspace.fingerprint(self.cfg)
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "setup = echo setup >> setup.log", "setup = echo other >> setup.log"), encoding="utf-8")
        self.assertNotEqual(workspace.fingerprint(config.load(self.paths)), before)

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

    def configure(self, factory="", commands="", extra=""):
        text = self.paths.project.read_text().replace("[factory]\n", f"[factory]\n{factory}")
        text = text.replace("echo = echo\n", f"echo = echo\n{commands}").replace("[events]\n", f"{extra}[events]\n")
        self.sb.write("project.md", text, base=self.paths.factory)
        return config.load(self.paths)

    def concurrently(self, *calls):
        threads = [threading.Thread(target=fn, args=args) for fn, args in calls]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_two_different_serial_commands_never_overlap_across_jobs(self):
        log = self.sb.tmp / "shared.log"
        cfg = self.configure("serial = migrate, seed\n",
                             f"migrate = echo m-start >> {log} && sleep 0.3 && echo m-end >> {log}\n"
                             f"seed = echo s-start >> {log} && sleep 0.3 && echo s-end >> {log}\n")
        for job in (self.job, self.other):
            self.sb.open_job(job)
        self.concurrently((workspace.run, (self.paths, cfg, self.job, "migrate", [])),
                          (workspace.run, (self.paths, cfg, self.other, "seed", [])))
        words = log.read_text().split()
        self.assertIn(words, (["m-start", "m-end", "s-start", "s-end"], ["s-start", "s-end", "m-start", "m-end"]))

    def test_an_independent_command_still_runs_alongside_a_serial_one(self):
        log = self.sb.tmp / "shared.log"
        cfg = self.configure("serial = migrate\n",
                             f"migrate = echo m-start >> {log} && sleep 0.4 && echo m-end >> {log}\n"
                             f"lintish = sleep 0.1 && echo lint >> {log}\n")
        for job in (self.job, self.other):
            self.sb.open_job(job)
        self.concurrently((workspace.run, (self.paths, cfg, self.job, "migrate", [])),
                          (workspace.run, (self.paths, cfg, self.other, "lintish", [])))
        self.assertEqual(log.read_text().split(), ["m-start", "lint", "m-end"])

    def test_one_port_per_job_shared_by_its_commands_and_never_by_another_job(self):
        a, b = self.sb.open_job(self.job), self.sb.open_job(self.other)
        port = lambda job: workspace.run(self.paths, self.cfg, job, "echo", ["$FACTORY_PORT"])["port"]
        self.assertEqual(port(self.job), port(self.job))
        self.assertNotEqual(port(self.job), port(self.other))

    def test_a_port_someone_else_took_is_replaced(self):
        self.sb.open_job()
        first = workspace.job_port(self.paths, self.job)
        with socket.socket() as squatter:
            squatter.bind(("127.0.0.1", first))
            squatter.listen()
            self.assertNotEqual(workspace.job_port(self.paths, self.job), first)

    SERVER = "python3 -m http.server $FACTORY_PORT --bind 127.0.0.1"
    PROBE = ("python3 -c \"import os, urllib.request; "
             "urllib.request.urlopen('http://127.0.0.1:' + os.environ['FACTORY_PORT'], timeout=5); print('served')\"")

    def test_the_server_starts_on_the_jobs_port_for_its_tests_and_stops_after(self):
        cfg = self.configure(commands=f"e2e = {self.PROBE}\n",
                             extra=f"[server]\ncommand = {self.SERVER}\nfor = e2e\ntimeout = 20\n\n")
        self.sb.open_job()
        result = workspace.run(self.paths, cfg, self.job, "e2e", [])
        self.assertEqual(result["exit_code"], 0, result["tail"])
        self.assertIn("served", result["tail"])
        self.assertFalse(workspace._port_in_use(result["port"]))

    def test_two_jobs_run_their_servers_and_tests_at_once_on_their_own_ports(self):
        cfg = self.configure(commands=f"e2e = {self.PROBE}\n",
                             extra=f"[server]\ncommand = {self.SERVER}\nfor = e2e\ntimeout = 20\n\n")
        for job in (self.job, self.other):
            self.sb.open_job(job)
        results = {}
        self.concurrently(*[(lambda j: results.__setitem__(j, workspace.run(self.paths, cfg, j, "e2e", [])), (j,))
                            for j in (self.job, self.other)])
        self.assertEqual([r["exit_code"] for r in results.values()], [0, 0])
        self.assertNotEqual(results[self.job]["port"], results[self.other]["port"])

    def test_a_server_that_never_listens_fails_the_command_and_is_stopped(self):
        cfg = self.configure(commands=f"e2e = {self.PROBE}\n",
                             extra="[server]\ncommand = sleep 30\nfor = e2e\ntimeout = 1\n\n")
        self.sb.open_job()
        result = workspace.run(self.paths, cfg, self.job, "e2e", [])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("did not listen", result["tail"])

    def test_a_test_command_may_start_its_own_server_on_the_port(self):
        cfg = self.configure(commands=f"selfserved = {self.SERVER} & sleep 1 && {self.PROBE}; kill %1\n")
        self.sb.open_job()
        self.assertIn("served", workspace.run(self.paths, cfg, self.job, "selfserved", [])["tail"])

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

    def test_probe_names_what_setup_leaves_that_git_does_not_ignore(self):
        self.assertEqual(workspace.probe(self.paths, self.cfg)["dirty"], [])
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "setup = echo setup >> setup.log", "setup = echo x > node_modules.tmp"), encoding="utf-8")
        result = workspace.probe(self.paths, config.load(self.paths))
        self.assertEqual((result["green"], result["dirty"]), (True, ["node_modules.tmp"]))


if __name__ == "__main__":
    unittest.main()
