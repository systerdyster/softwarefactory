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

    def test_commands_refuse_forms_cross_origin_and_unknown_verbs(self):
        body = json.dumps({"command": "approve", "job": self.job})
        self.assertEqual(self.request("POST", "/command", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/command", body, {"Content-Type": "application/json",
                                                                  "Origin": "https://evil.example"})[0], 403)
        bad = json.dumps({"command": "merge", "job": self.job})
        self.assertEqual(self.request("POST", "/command", bad, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(actions.inbox_list(self.paths), [])
