"""Test T2: the translator acts only on signed, fresh, allowlisted private-channel requests."""

import json
import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path

from stormshow import protocol as P
from stormshow.mesh import BROADCAST
from stormshow.seqstate import ReceiveWindow
from stormshow.translator.service import BoltCounter, in_show_hours
from tests.helpers import EDGE, KEY, OTHER_KEY, Rig


def req(seq, verb, level=None, key=KEY):
    return P.Request(seq, verb, level).encode(key)


class Rejections(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def tearDown(self):
        self.rig.close()

    def settle(self):
        self.rig.mesh.settle()
        self.rig.translator.drain()
        self.rig.mesh.settle()

    def replies(self):
        out = []
        for src, _dst, _ch, text in self.rig.mesh.sent:
            if src == self.rig.porch.node_id:
                out.append(P.parse_reply(text, KEY))
        return out

    def test_accepts_valid_request_and_replies(self):
        self.rig.edge.send_text(req(1, "STRIKE", 80), BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [("STRIKE", 0.8)])
        self.assertEqual(self.replies(), [P.Reply(1, "OK", 1)])

    def test_replies_use_the_hop_limit(self):
        """F-14: private traffic isn't relayed across the neighbourhood."""
        self.rig.edge.send_text(req(1, "PING"), BROADCAST, 1)
        self.settle()
        idx = [i for i, s in enumerate(self.rig.mesh.sent) if s[0] == self.rig.porch.node_id][0]
        self.assertEqual(self.rig.mesh.hop_limits[idx], 1)

    def test_ignores_public_channel(self):
        self.rig.edge.send_text(req(1, "STRIKE", 80), BROADCAST, 0)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.rig.translator.stats["dropped"], 0)

    def test_drops_unsigned_and_wrongly_signed(self):
        """F-01: the channel key alone is not enough."""
        self.rig.edge.send_text("SB1 00aa STRIKE 80", BROADCAST, 1)
        self.rig.edge.send_text(req(1, "STRIKE", 80, key=OTHER_KEY), BROADCAST, 1)
        line = req(2, "STRIKE", 80)
        self.rig.edge.send_text(line.replace("STRIKE 80", "STRIKE 99"), BROADCAST, 1)
        self.rig.edge.send_text("RM -RF", BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.rig.translator.stats["dropped"], 4)
        self.assertEqual(self.replies(), [])

    def test_drops_non_allowlisted_sender(self):
        self.rig.stranger.send_text(req(1, "STRIKE", 80), BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.replies(), [])

    def test_replay_runs_once(self):
        """F-01: replaying a captured, validly signed request does nothing new."""
        line = req(7, "HEART")
        self.rig.edge.send_text(line, BROADCAST, 1)
        self.settle()
        self.rig.tr_clock.t += 5
        self.rig.edge.send_text(line, BROADCAST, 1)
        self.settle()
        self.assertEqual(len(self.rig.ha.calls), 1)
        self.assertEqual(self.replies(), [P.Reply(7, "OK", 1), P.Reply(7, "OK", 1)])  # cached reply resent

    def test_replay_after_restart_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "link.json")
            w = ReceiveWindow(path)
            w.accept(500)
            self.rig.translator.window = ReceiveWindow(path)  # as after a reboot
            self.rig.edge.send_text(req(400, "STRIKE", 80), BROADCAST, 1)
            self.settle()
            self.assertEqual(self.rig.ha.calls, [])
            self.assertEqual(self.replies(), [P.Reply(400, "STALE", 500)])

    def test_ping_takes_no_action_and_is_limited(self):
        for seq in range(1, 6):
            self.rig.edge.send_text(req(seq, "PING"), BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(len(self.replies()), 2)  # F-15: ping bucket burst 2

    def test_closed_when_show_off(self):
        self.rig.ha.states["input_boolean.storm_show_running"] = "off"
        self.rig.edge.send_text(req(3, "STORM"), BROADCAST, 1)
        self.settle()
        self.assertEqual(self.rig.ha.calls, [])
        self.assertEqual(self.replies(), [P.Reply(3, "CLOSED")])

    def test_local_show_hours(self):
        self.rig.translator.cfg.show_entity = None
        self.rig.translator.cfg.show_hours = (time(18, 0), time(22, 0))
        self.rig.translator.wall_clock = lambda: datetime(2026, 10, 31, 23, 30)
        self.rig.edge.send_text(req(3, "STORM"), BROADCAST, 1)
        self.settle()
        self.assertEqual(self.replies(), [P.Reply(3, "CLOSED")])

    def test_err_when_ha_down(self):
        self.rig.ha.fail = True
        self.rig.edge.send_text(req(4, "FIRE"), BROADCAST, 1)
        self.settle()
        self.assertEqual(self.replies(), [P.Reply(4, "ERR")])

    def test_own_rate_limit_and_quiet_busy(self):
        for seq in range(1, 11):
            self.rig.edge.send_text(req(seq, "STRIKE", 50), BROADCAST, 1)
        self.settle()
        self.assertEqual(len(self.rig.ha.calls), 5)  # burst = 5
        busy = [r for r in self.replies() if r.status == "BUSY"]
        self.assertLessEqual(len(busy), 2)  # F-15: BUSY replies are themselves limited

    def test_queue_is_bounded(self):
        self.rig.translator.cfg.max_queue = 2
        self.rig.translator._bucket.tokens = 100
        for seq in range(1, 20):
            self.rig.edge.send_text(req(seq, "STRIKE", 50), BROADCAST, 1)
        self.settle()
        self.assertGreater(self.rig.translator.stats["overflow"], 0)

    def test_allowlist_is_the_edge(self):
        self.assertEqual(self.rig.translator.cfg.edge_node_ids, [EDGE])


class ShowHours(unittest.TestCase):
    def test_ranges(self):
        evening = (time(18, 0), time(22, 0))
        self.assertTrue(in_show_hours(evening, datetime(2026, 10, 31, 19, 0)))
        self.assertFalse(in_show_hours(evening, datetime(2026, 10, 31, 22, 0)))
        overnight = (time(20, 0), time(1, 0))
        self.assertTrue(in_show_hours(overnight, datetime(2026, 11, 1, 0, 30)))
        self.assertFalse(in_show_hours(overnight, datetime(2026, 11, 1, 12, 0)))
        self.assertTrue(in_show_hours(None, datetime(2026, 11, 1, 12, 0)))


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
