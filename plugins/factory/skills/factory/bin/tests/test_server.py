import http.client
import importlib.util
import json
import threading
from pathlib import Path

from support import SandboxTest

from fx import actions, board

_spec = importlib.util.spec_from_file_location("factory_server", Path(__file__).resolve().parents[2] / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


class ServerTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.httpd, self.token, self.port = server.serve(self.paths, 0, token="t0ken")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def request(self, method, path, body=None, headers=None, token="t0ken"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"X-Factory-Token": token} if token else {}
        h.update(headers or {})
        conn.request(method, path, body=body, headers=h)
        res = conn.getresponse()
        data = res.read().decode()
        conn.close()
        return res.status, data

    def test_every_request_needs_the_token(self):
        self.assertEqual(self.request("GET", "/", token=None)[0], 403)
        self.assertEqual(self.request("GET", "/", token="wrong")[0], 403)
        self.assertEqual(self.request("GET", "/?token=t0ken", token=None)[0], 200)
        self.assertEqual(self.request("GET", "/board.json")[0], 200)

    def test_the_board_is_read_from_the_jobs_not_from_the_orchestrators_last_write(self):
        self.assertFalse(self.paths.board.exists())
        status, text = self.request("GET", "/board.json")
        card = json.loads(text)["jobs"][0]
        self.assertEqual((status, card["id"], card["stage"]), (200, self.job, "queued"))
        self.assertEqual([s["key"] for s in card["pipeline"]], ["start", "build", "verify", "tidy", "review", "approve", "pr"])

    def test_a_job_carries_the_main_reviewers_latest_confidence(self):
        jd = self.paths.job(self.job)
        card = lambda: board.build(self.paths, self.cfg, write=False)["jobs"][0]
        self.assertIsNone(card()["confidence"])
        self.sb.write("round-1/review-code.md", "VERDICT: CHANGES\n\nConfidence: 4/10 — the cache key\n", base=jd)
        self.sb.write("round-2/review-code.md", "VERDICT: PASS\n\nConfidence: 8/10 — only the date format\n", base=jd)
        self.assertEqual(card()["confidence"], 8)

    def test_factory_cli_dashboard_serves_and_prints_its_url_first(self):
        import os
        import subprocess
        import sys
        cli = Path(__file__).resolve().parents[1] / "factory.py"
        proc = subprocess.Popen([sys.executable, str(cli), "dashboard"], stdout=subprocess.PIPE, text=True,
                                env=dict(os.environ, FACTORY_ROOT=str(self.paths.root)))
        try:
            url = proc.stdout.readline().strip().split(": ", 1)[1]
            port, token = url.split(":")[2].split("/")[0], url.split("token=")[1]
            conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=5)
            conn.request("GET", "/board.json", headers={"X-Factory-Token": token})
            res = conn.getresponse()
            board = json.loads(res.read())
            self.assertEqual((res.status, board["jobs"][0]["id"], board["orchestrator"]), (200, self.job, {"state": "none"}))
            conn.close()
            again = subprocess.run([sys.executable, str(cli), "dashboard"], capture_output=True, text=True, timeout=10,
                                   env=dict(os.environ, FACTORY_ROOT=str(self.paths.root)))
            self.assertIn(f"Factory dashboard: {url}", again.stdout)
            self.assertIn('"already running"', again.stdout)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()
        self.assertFalse(self.paths.dashboard.exists())

    def test_a_queued_job_says_which_jobs_it_waits_for(self):
        later = self.sb.queue_job("002-later", "Later", after=[self.job])
        cards = {c["id"]: c for c in json.loads(self.request("GET", "/board.json")[1])["jobs"]}
        self.assertEqual((cards[later]["waits_for"], cards[self.job]["waits_for"]), ([self.job], []))

    def test_a_board_that_cant_be_computed_is_served_from_the_saved_one_and_marked_stale(self):
        from fx import board
        board.build(self.paths, self.cfg)
        self.paths.project.write_text("no configuration here\n", encoding="utf-8")
        served = json.loads(self.request("GET", "/board.json")[1])
        self.assertEqual(served["jobs"][0]["id"], self.job)
        self.assertIn("project.md", served["stale"])

    def test_an_orchestrator_heard_from_lately_is_running_and_a_silent_one_stale(self):
        from fx import actions, board, jobs
        actions.acquire_lease(self.paths)
        self.assertEqual(board.orchestrator(self.paths)["state"], "running")
        lease = jobs.read_json(self.paths.orchestrator)
        jobs.write_json(self.paths.orchestrator, {**lease, "seen_at": "2026-01-01T00:00:00+00:00"})
        self.assertEqual(board.orchestrator(self.paths)["state"], "stale")

    def test_a_record_left_by_a_dead_dashboard_is_ignored(self):
        self.paths.dashboard.write_text(json.dumps({"url": "http://127.0.0.1:1/?token=x", "port": 1, "pid": 999999}))
        self.assertIsNone(server.running(self.paths))
        self.assertFalse(self.paths.dashboard.exists())

    def test_a_foreign_host_header_is_refused(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)

    def test_job_files_are_served_and_nothing_outside_them(self):
        status, text = self.request("GET", f"/file?job={self.job}&path=spec.md")
        self.assertEqual((status, "The answer is 42." in text), (200, True))
        self.assertEqual(self.request("GET", f"/file?job={self.job}&path=../../project.md")[0], 404)
        self.assertEqual(self.request("GET", "/file?job=..&path=project.md")[0], 400)

    def post(self, command, text=""):
        body = json.dumps({"command": command, "job": self.job, "text": text, "by": "dashboard"})
        return self.request("POST", "/command", body, {"Content-Type": "application/json"})

    def commands(self):
        status, data = self.request("GET", f"/commands?job={self.job}")
        self.assertEqual(status, 200)
        return json.loads(data)["commands"]

    def test_a_command_the_job_cannot_take_is_refused_at_once_and_reads_as_refused(self):
        status, data = self.post("rework", "Use the cache")
        self.assertEqual(status, 400)
        self.assertIn("rework is for needs-human", json.loads(data)["error"])
        latest = self.commands()[-1]
        self.assertEqual((latest["command"], latest["status"], latest["text"]), ("rework", "refused", "Use the cache"))
        self.assertEqual(actions.inbox_list(self.paths), [])

    def test_a_cancel_waits_for_the_orchestrator_and_a_second_one_is_refused_meanwhile(self):
        status, data = self.post("cancel", "duplicate")
        self.assertEqual(status, 202)
        self.assertEqual([(c["command"], c["status"]) for c in self.commands()], [("cancel", "queued")])
        self.assertEqual(self.post("cancel")[0], 409)
        actions.inbox_done(self.paths, json.loads(data)["queued"], "cancelled")
        latest = self.commands()[-1]
        self.assertEqual((latest["status"], latest["outcome"]), ("applied", "cancelled"))

    def test_commands_lists_only_the_asked_job(self):
        self.post("cancel")
        actions.give_command(self.paths, self.cfg, "cancel", "002-other", "", "terminal")
        self.assertEqual([c["job"] for c in self.commands()], [self.job])

    def test_commands_refuse_forms_cross_origin_and_unknown_verbs(self):
        body = json.dumps({"command": "approve", "job": self.job})
        self.assertEqual(self.request("POST", "/command", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/command", body, {"Content-Type": "application/json",
                                                                  "Origin": "https://evil.example"})[0], 403)
        bad = json.dumps({"command": "merge", "job": self.job})
        self.assertEqual(self.request("POST", "/command", bad, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(actions.inbox_list(self.paths), [])
