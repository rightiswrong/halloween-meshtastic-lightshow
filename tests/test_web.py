"""Test T5 on a PC: the captive portal, the page and the summon API."""

import http.client
import json
import threading
import unittest

from stormshow.bbs.web import make_server
from tests.helpers import Rig


class Portal(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.server = make_server(self.rig.bbs, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.rig.close()

    def req(self, method, path, body=None, host="portal.test", ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = ctype
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp, payload

    def test_captive_probes_redirect_to_portal(self):
        for host, path in (
            ("captive.apple.com", "/hotspot-detect.html"),
            ("connectivitycheck.gstatic.com", "/generate_204"),
            ("www.msftconnecttest.com", "/connecttest.txt"),
            ("portal.test", "/anything"),
        ):
            with self.subTest(host=host):
                resp, _ = self.req("GET", path, host=host)
                self.assertEqual(resp.status, 302)
                self.assertEqual(resp.getheader("Location"), "http://portal.test/")

    def test_page_is_self_contained(self):
        resp, body = self.req("GET", "/")
        self.assertEqual(resp.status, 200)
        html = body.decode()
        self.assertIn('data-cmd="STRIKE"', html)
        self.assertNotIn("http://", html.replace("http://portal.test", ""))  # no external assets
        self.assertNotIn("https://", html)
        self.assertIn("default-src 'self'", resp.getheader("Content-Security-Policy"))

    def test_summon_and_cooldown(self):
        resp, body = self.req("POST", "/api/summon", {"command": "STRIKE"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        resp, body = self.req("POST", "/api/summon", {"command": "STORM"})
        self.assertEqual(resp.status, 429)
        self.assertEqual(json.loads(body)["retry_after"], 30)
        resp, body = self.req("GET", "/api/status")
        self.assertIn("a guest at the gate", json.loads(body)["who"])

    def test_bad_requests(self):
        self.assertEqual(self.req("POST", "/api/summon", b"not json")[0].status, 400)
        self.assertEqual(self.req("POST", "/api/summon", {"cmd": "STRIKE"})[0].status, 400)
        self.assertEqual(self.req("POST", "/api/summon", {"command": 5})[0].status, 400)
        self.assertEqual(self.req("POST", "/api/summon", b"x" * 1000)[0].status, 400)
        self.assertEqual(self.req("POST", "/api/other", {"command": "STRIKE"})[0].status, 404)
        self.assertEqual(self.req("POST", "/api/summon", {"command": "STRIKE"}, host="evil.test")[0].status, 404)
        resp, body = self.req("POST", "/api/summon", {"command": "<script>"})
        self.assertEqual(json.loads(body)["status"], "unknown")
        self.assertEqual(self.rig.ha.calls, [])


if __name__ == "__main__":
    unittest.main()
