"""The real HA clients (webhook and token) against a local stand-in server."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from stormshow.translator.ha import HAError, TokenHA, WebhookHA

TOKEN = "secret-token"
HOOK = "storm-0123456789abcdef0123456789abcdef"


class FakeHA(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _reply(self, code, obj=None):
        body = json.dumps(obj or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._reply(401)
            return False
        return True

    def do_GET(self):
        if not self._authed():
            return
        if self.path == "/api/":
            return self._reply(200, {"message": "API running."})
        if self.path == "/api/states/input_boolean.storm_show_running":
            return self._reply(200, {"state": "on"})
        return self._reply(404, {"message": "Entity not found."})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith("/api/webhook/"):
            FakeHA.calls.append((self.path, body, self.headers.get("Authorization")))
            return self._reply(200)  # HA answers 200 to webhooks
        if not self._authed():
            return
        FakeHA.calls.append((self.path, body, "token"))
        return self._reply(200, [])


class Clients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_webhook_sends_no_token(self):
        """F-02: webhook mode carries only verb and strength, and no credentials."""
        ha = WebhookHA(self.url, HOOK)
        ha.trigger("STRIKE", 0.8)
        path, body, auth = FakeHA.calls[-1]
        self.assertEqual(path, f"/api/webhook/{HOOK}")
        self.assertEqual(body, {"verb": "STRIKE", "strength": 0.8})
        self.assertIsNone(auth)
        self.assertFalse(ha.has_state)
        with self.assertRaises(HAError):
            ha.state("lock.front_door")
        self.assertNotIn(HOOK, repr(ha))

    def test_token_mode(self):
        ha = TokenHA(self.url, TOKEN, {"STRIKE": "script.storm_strike"})
        self.assertEqual(ha.check(), "API running.")
        self.assertEqual(ha.state("input_boolean.storm_show_running"), "on")
        self.assertIsNone(ha.state("input_boolean.nope"))
        ha.trigger("STRIKE", 0.8)
        self.assertEqual(
            FakeHA.calls[-1][:2],
            ("/api/services/script/turn_on", {"entity_id": "script.storm_strike", "variables": {"strength": 0.8}}),
        )
        self.assertNotIn(TOKEN, repr(ha))

    def test_bad_token(self):
        with self.assertRaises(HAError):
            TokenHA(self.url, "wrong", {}).check()

    def test_unreachable(self):
        with self.assertRaises(HAError):
            WebhookHA("http://127.0.0.1:9", HOOK, timeout_s=1).trigger("STRIKE", 0.5)


if __name__ == "__main__":
    unittest.main()
