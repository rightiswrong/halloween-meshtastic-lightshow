"""Test T5 on a PC: the captive portal, the page and the summon API."""

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest

from stormshow.bbs.web import LeaseTable, make_server
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
        csp = resp.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)  # F-20
        self.assertNotIn("<script>", html)
        self.assertIn("never asks for passwords", html)  # F-09

    def test_assets(self):
        for path, ctype in (("/app.js", "text/javascript"), ("/app.css", "text/css")):
            resp, body = self.req("GET", path)
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.getheader("Content-Type").startswith(ctype))
            self.assertGreater(len(body), 100)

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


class Limits(unittest.TestCase):
    def test_connection_cap(self):
        """F-07: one client's 200 idle sockets must not each get a thread."""
        rig = Rig(edge_kw={"max_connections": 8})
        server = make_server(rig.bbs, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            before = threading.active_count()
            socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(200)]
            time.sleep(0.5)
            self.assertLessEqual(threading.active_count() - before, 8)
            self.assertGreater(server.refused, 150)
            for s in socks:
                s.close()
            time.sleep(0.3)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/", headers={"Host": "portal.test"})
            self.assertEqual(conn.getresponse().status, 200)  # serves again once they're gone
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            rig.close()

    def test_lease_table_maps_ip_to_mac(self):
        """F-10: changing IP by hand doesn't reset the cooldown if the MAC is known."""
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("1761400000 aa:bb:cc:dd:ee:ff 10.66.0.20 phone *\n")
            fh.write("1761400000 11:22:33:44:55:66 10.66.0.21 * 01:11:22:33:44:55:66\n")
        table = LeaseTable(fh.name)
        self.assertEqual(table.mac_for("10.66.0.20"), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(table.mac_for("10.66.0.21"), "11:22:33:44:55:66")
        self.assertIsNone(table.mac_for("10.66.0.99"))
        self.assertIsNone(LeaseTable(None).mac_for("10.66.0.20"))


if __name__ == "__main__":
    unittest.main()
