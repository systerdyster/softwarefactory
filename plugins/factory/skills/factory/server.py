#!/usr/bin/env python3
"""The factory dashboard and inbox. Python standard library only.

    factory-dashboard [--port N]        (the plugin puts factory-dashboard on PATH)

Binds to 127.0.0.1 only and prints a URL with a random token; every request must carry that token
(the page sends it as a header). Serves dashboard.html, factory/board.json and the markdown files in
job folders, and accepts approve, rework and cancel by writing a command file to factory/inbox/,
which the orchestrator runs exactly like its terminal form. It never changes a job itself."""

import argparse
import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

from fx import actions, jobs  # noqa: E402
from fx.errors import FactoryError  # noqa: E402
from fx.paths import Paths  # noqa: E402

HERE = Path(__file__).resolve().parent
MAX_BODY = 16 * 1024
READABLE = (".md", ".json")


def make_handler(paths, token, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "factory"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj))

        def _authorised(self):
            if self.headers.get("Host") not in allowed_hosts:
                return False
            supplied = self.headers.get("X-Factory-Token") or parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return hmac.compare_digest(supplied, token)

        def do_GET(self):
            if not self._authorised():
                return self._json(403, {"error": "missing or wrong token"})
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/board.json":
                if not paths.board.exists():
                    return self._json(200, {"jobs": [], "stages": [], "updated_at": None,
                                            "note": "no board yet: the orchestrator writes it"})
                return self._send(200, paths.board.read_bytes())
            if url.path == "/file":
                q = parse_qs(url.query)
                return self._file(q.get("job", [""])[0], q.get("path", [""])[0])
            return self._json(404, {"error": "not found"})

        def _file(self, job_id, rel):
            try:
                jobs.check_id(job_id)
            except FactoryError as e:
                return self._json(400, {"error": str(e)})
            jd = paths.job(job_id).resolve()
            target = (jd / rel).resolve()
            if not str(target).startswith(str(jd) + os.sep) or target.suffix not in READABLE or not target.is_file():
                return self._json(404, {"error": "no such job file"})
            return self._send(200, target.read_bytes(), "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._authorised():
                return self._json(403, {"error": "missing or wrong token"})
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc not in allowed_hosts:
                return self._json(403, {"error": "cross-origin request refused"})
            if urlparse(self.path).path != "/command":
                return self._json(404, {"error": "not found"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(415, {"error": "expected application/json"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(413, {"error": "body missing or too large"})
            try:
                body = json.loads(self.rfile.read(length))
                result = actions.write_command(paths, str(body.get("command", "")), str(body.get("job", "")),
                                               str(body.get("text", ""))[:4000], str(body.get("by", ""))[:200])
            except (json.JSONDecodeError, AttributeError):
                return self._json(400, {"error": "invalid JSON"})
            except FactoryError as e:
                return self._json(400, {"error": str(e)})
            return self._json(202, result)

        do_PUT = do_DELETE = do_PATCH = lambda self: self._json(405, {"error": "method not allowed"})

    return Handler


def serve(paths, port=0, token=None):
    token = token or secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), None)
    bound = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(paths, token, bound)
    return httpd, token, bound


def main():
    ap = argparse.ArgumentParser(description="Factory dashboard on 127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    args = ap.parse_args()
    httpd, token, port = serve(Paths(), args.port)
    print(f"Factory dashboard: http://127.0.0.1:{port}/?token={token}", flush=True)
    print("Ctrl-C to stop. Anyone with this URL can queue approve/rework/cancel commands.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
