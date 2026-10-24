"""Test T2: the translator acts only on allowlisted, well-formed, private-channel requests."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from stormshow.mesh import BROADCAST
from stormshow.translator.service import BoltCounter
from tests.helpers import EDGE, Rig


class Rejections(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def tearDown(self):
        self.rig.close()

    def settle(self):
        self.rig.mesh.settle()
        self.rig.translator.drain()
        self.rig.mesh.settle()

    def test_accepts_valid_request_and_replies(self):
        self.rig.edge.send_text("SB1 00aa STRIKE 80", BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [("script.storm_strike", {"strength": 0.8})])
        self.assertIn((self.rig.porch.node_id, BROADCAST, 1, "SB1 00aa OK 1"), self.rig.mesh.sent)

    def test_ignores_public_channel(self):
        self.rig.edge.send_text("SB1 00aa STRIKE 80", BROADCAST, 0)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.rig.translator.stats["dropped"], 0)  # silently, it's just chatter

    def test_drops_unknown_command(self):
        self.rig.edge.send_text("RM -RF", BROADCAST, 1)
        self.rig.edge.send_text("SB1 00aa DANCE", BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.rig.translator.stats["dropped"], 2)

    def test_drops_non_allowlisted_sender(self):
        self.rig.stranger.send_text("SB1 00aa STRIKE 80", BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.rig.translator.stats["dropped"], 1)
        replies = [s for s in self.rig.mesh.sent if s[0] == self.rig.porch.node_id]
        self.assertEqual(replies, [])  # no reply either: don't confirm anything to strangers

    def test_ping_takes_no_action(self):
        self.rig.edge.send_text("SB1 0001 PING", BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertIn((self.rig.porch.node_id, BROADCAST, 1, "SB1 0001 PONG"), self.rig.mesh.sent)

    def test_closed_when_show_off(self):
        self.rig.ha.states["input_boolean.storm_show_running"] = "off"
        self.rig.edge.send_text("SB1 00ab STORM", BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertIn((self.rig.porch.node_id, BROADCAST, 1, "SB1 00ab CLOSED"), self.rig.mesh.sent)

    def test_err_when_ha_down(self):
        self.rig.ha.fail = True
        self.rig.edge.send_text("SB1 00ac FIRE", BROADCAST, 1)
        self.settle()
        self.assertIn((self.rig.porch.node_id, BROADCAST, 1, "SB1 00ac ERR"), self.rig.mesh.sent)

    def test_duplicate_runs_once_and_resends_reply(self):
        self.rig.edge.send_text("SB1 00ad HEART", BROADCAST, 1)
        self.settle()
        self.rig.edge.send_text("SB1 00ad HEART", BROADCAST, 1)
        self.settle()
        self.assertEqual(len(self.rig.ha.calls), 1)
        oks = [s for s in self.rig.mesh.sent if s[3] == "SB1 00ad OK 1"]
        self.assertEqual(len(oks), 2)

    def test_own_rate_limit(self):
        for i in range(8):
            self.rig.edge.send_text(f"SB1 10{i:02x} STRIKE 50", BROADCAST, 1)
        self.settle()
        self.assertEqual(len(self.rig.ha.calls), 5)  # burst = 5
        busy = [s for s in self.rig.mesh.sent if s[3].endswith("BUSY")]
        self.assertEqual(len(busy), 3)

    def test_allowlist_is_the_edge(self):
        self.assertEqual(self.rig.translator.cfg.edge_node_ids, [EDGE])


class Counter(unittest.TestCase):
    def test_resets_each_night_and_persists(self):
        now = [datetime(2026, 10, 31, 19, 0)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bolts.json"
            c = BoltCounter(str(path), 12, now=lambda: now[0])
            self.assertEqual(c.increment(), 1)
            self.assertEqual(c.increment(), 2)
            now[0] = datetime(2026, 11, 1, 1, 0)  # after midnight, same night
            self.assertEqual(BoltCounter(str(path), 12, now=lambda: now[0]).increment(), 3)
            now[0] = datetime(2026, 11, 1, 13, 0)  # past noon: new night
            c2 = BoltCounter(str(path), 12, now=lambda: now[0])
            self.assertEqual(c2.increment(), 1)
            self.assertEqual(json.loads(path.read_text())["bolts"], 1)


if __name__ == "__main__":
    unittest.main()
