"""The real HA REST client against a local stand-in server."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from stormshow.translator.ha import HAError, HomeAssistant

TOKEN = "secret-token"


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
        if not self._authed():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHA.calls.append((self.path, body))
        return self._reply(200, [])


class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_check_state_and_script(self):
        ha = HomeAssistant(self.url, TOKEN)
        self.assertEqual(ha.check(), "API running.")
        self.assertEqual(ha.state("input_boolean.storm_show_running"), "on")
        self.assertIsNone(ha.state("input_boolean.nope"))
        ha.run_script("script.storm_strike", {"strength": 0.8})
        self.assertEqual(
            FakeHA.calls[-1],
            ("/api/services/script/turn_on", {"entity_id": "script.storm_strike", "variables": {"strength": 0.8}}),
        )

    def test_bad_token(self):
        with self.assertRaises(HAError):
            HomeAssistant(self.url, "wrong").check()

    def test_unreachable(self):
        with self.assertRaises(HAError):
            HomeAssistant("http://127.0.0.1:9", TOKEN, timeout_s=1).check()


if __name__ == "__main__":
    unittest.main()
