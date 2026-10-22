import http.client
import importlib.util
import json
import threading
from pathlib import Path

from support import SandboxTest

from fx import actions

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
            self.assertEqual((res.status, json.loads(res.read())["jobs"][0]["id"]), (200, self.job))
            conn.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()

    def test_a_foreign_host_header_is_refused(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)

    def test_job_files_are_served_and_nothing_outside_them(self):
        status, text = self.request("GET", f"/file?job={self.job}&path=spec.md")
        self.assertEqual((status, "The answer is 42." in text), (200, True))
        self.assertEqual(self.request("GET", f"/file?job={self.job}&path=../../project.md")[0], 404)
        self.assertEqual(self.request("GET", "/file?job=..&path=project.md")[0], 400)

    def test_commands_land_in_the_inbox(self):
        body = json.dumps({"command": "rework", "job": self.job, "text": "Use the cache", "by": "dashboard"})
        status, _ = self.request("POST", "/command", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 202)
        pending = actions.inbox_list(self.paths)
        self.assertEqual((pending[0]["command"], pending[0]["text"]), ("rework", "Use the cache"))

    def post(self, command, text=""):
        body = json.dumps({"command": command, "job": self.job, "text": text, "by": "dashboard"})
        return self.request("POST", "/command", body, {"Content-Type": "application/json"})

    def commands(self):
        status, data = self.request("GET", f"/commands?job={self.job}")
        self.assertEqual(status, 200)
        return json.loads(data)["commands"]

    def test_a_second_identical_command_is_refused_while_the_first_waits(self):
        self.assertEqual(self.post("approve")[0], 202)
        status, data = self.post("approve")
        self.assertEqual(status, 409)
        self.assertIn("already queued", json.loads(data)["error"])
        self.assertEqual(len(actions.inbox_list(self.paths)), 1)

    def test_a_command_reads_as_queued_then_applied(self):
        name = json.loads(self.post("approve")[1])["queued"]
        self.assertEqual([(c["command"], c["status"]) for c in self.commands()], [("approve", "queued")])
        actions.inbox_done(self.paths, name, "approved at head abc")
        latest = self.commands()[-1]
        self.assertEqual((latest["status"], latest["outcome"]), ("applied", "approved at head abc"))

    def test_a_refused_command_reads_as_refused_and_frees_the_slot(self):
        name = json.loads(self.post("approve")[1])["queued"]
        actions.inbox_done(self.paths, name, "already approved; PR 63 is open", refused=True)
        self.assertEqual(self.commands()[-1]["status"], "refused")
        self.assertEqual(self.post("approve")[0], 202)

    def test_commands_lists_only_the_asked_job(self):
        self.post("approve")
        actions.write_command(self.paths, "approve", "002-other", "", "terminal")
        self.assertEqual([c["job"] for c in self.commands()], [self.job])

    def test_commands_refuse_forms_cross_origin_and_unknown_verbs(self):
        body = json.dumps({"command": "approve", "job": self.job})
        self.assertEqual(self.request("POST", "/command", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/command", body, {"Content-Type": "application/json",
                                                                  "Origin": "https://evil.example"})[0], 403)
        bad = json.dumps({"command": "merge", "job": self.job})
        self.assertEqual(self.request("POST", "/command", bad, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(actions.inbox_list(self.paths), [])
