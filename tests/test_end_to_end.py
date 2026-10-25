"""Edge BBS <-> simulated LoRa <-> translator <-> fake HA (tests T4 and T7 without radios)."""

import tempfile
import time
import unittest
from pathlib import Path

from stormshow import protocol as P
from stormshow.bbs.store import Store
from stormshow.mesh import BROADCAST
from stormshow.seqstate import ReceiveWindow, SendCounter
from tests.helpers import EDGE, KEY, OTHER_KEY, PORCH, Rig


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def tearDown(self):
        self.rig.close()

    def test_web_summon_reaches_home_assistant(self):
        r = self.rig.bbs.summon("web:aa:bb:cc:dd:ee:01", "a guest", "strike")
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.bolts, 1)
        self.assertEqual(self.rig.ha.calls, [("STRIKE", 0.8)])
        r = self.rig.bbs.summon("web:aa:bb:cc:dd:ee:02", "a guest", "BOOM")
        self.assertEqual(r.bolts, 2)
        self.assertEqual(self.rig.ha.calls[-1], ("STRIKE", 1.0))

    def test_traffic_is_signed_and_sequenced(self):
        self.rig.bbs.summon("web:a", "g", "STORM")
        self.rig.bbs.summon("web:b", "g", "FIRE")
        requests = [P.parse_request(t, KEY) for src, _, ch, t in self.rig.mesh.sent if src == EDGE and ch == 1]
        self.assertEqual([r.verb for r in requests], ["STORM", "FIRE"])
        self.assertLess(requests[0].seq, requests[1].seq)
        self.assertTrue(all(h == 1 for h in self.rig.mesh.hop_limits))

    def test_every_guest_action_maps_to_a_verb(self):
        for i, word in enumerate(("STORM", "FIRE", "HEART")):
            r = self.rig.bbs.summon(f"web:{i}", "g", word)
            self.assertEqual(r.status, "ok", word)
        self.assertEqual(self.rig.ha.calls, [("STORM", None), ("FIRE", None), ("HEART", None)])

    def test_cooldown_per_guest_and_refund_on_failure(self):
        self.assertEqual(self.rig.bbs.summon("web:a", "g", "STRIKE").status, "ok")
        r = self.rig.bbs.summon("web:a", "g", "STRIKE")
        self.assertEqual(r.status, "cooldown")
        self.assertEqual(r.retry_after, 30)
        self.rig.ha.states["input_boolean.storm_show_running"] = "off"
        self.assertEqual(self.rig.bbs.summon("web:b", "g", "STRIKE").status, "closed")
        self.rig.ha.states["input_boolean.storm_show_running"] = "on"
        self.assertEqual(self.rig.bbs.summon("web:b", "g", "STRIKE").status, "ok")  # not charged

    def test_mesh_flood_cannot_starve_the_web(self):
        """F-05: spoofed mesh identities use up only the mesh budget."""
        rig = Rig(edge_kw={"mesh_per_minute": 2, "mesh_burst": 2, "web_per_minute": 6, "web_burst": 3})
        try:
            results = [rig.bbs.summon(f"mesh:!{i:08x}", "x", "STRIKE").status for i in range(1, 30)]
            self.assertEqual(results.count("ok"), 2)
            self.assertEqual(rig.bbs.summon("web:gate", "g", "STRIKE").status, "ok")
        finally:
            rig.close()

    def test_timeout_when_porch_is_silent(self):
        self.rig.mesh.drop = lambda src, dst, ch, text: src == PORCH
        r = self.rig.bbs.summon("web:a", "g", "STRIKE")
        self.assertEqual(r.status, "error")
        self.rig.mesh.drop = lambda *_: False
        self.assertEqual(self.rig.bbs.summon("web:a", "g", "STRIKE").status, "ok")  # refunded

    def test_radio_down(self):
        self.rig.edge.up = False
        self.assertEqual(self.rig.bbs.summon("web:a", "g", "STRIKE").status, "error")

    def test_ping(self):
        reply = self.rig.bbs.ping()
        self.assertEqual(reply.status, "PONG")
        self.assertEqual(self.rig.ha.calls, [])

    def test_edge_recovers_from_a_lost_counter(self):
        """STALE: a re-imaged edge jumps past the translator's high-water mark and retries."""
        with tempfile.TemporaryDirectory() as d:
            win_path = str(Path(d) / "link.json")
            w = ReceiveWindow(win_path)
            w.accept(1000)
            self.rig.translator.window = ReceiveWindow(win_path)
            self.rig.bbs.counter = SendCounter(str(Path(d) / "seq.json"))  # starts at 1 again
            r = self.rig.bbs.summon("web:a", "g", "STRIKE")
            self.assertEqual(r.status, "ok")
            self.assertGreater(self.rig.bbs.counter.next(), 1000)

    def test_menu_who_and_privacy(self):
        self.assertIn("STRIKE", self.rig.bbs.summon("web:a", "g", "?").message)
        self.assertIn("No one", self.rig.bbs.summon("web:a", "g", "WHO").message)
        self.rig.guest.send_text("1", EDGE, 0)  # menu number for STRIKE
        self.assertTrue(wait_for(lambda: self.rig.guest_inbox))
        who = self.rig.bbs.summon("web:a", "g", "who").message
        self.assertIn("a mesh visitor", who)  # F-17: mesh names hidden by default
        self.assertNotIn("Trick or Treater", who)
        self.assertEqual(self.rig.bbs.summon("web:a", "g", "rm -rf /").status, "unknown")
        self.assertEqual(len(self.rig.ha.calls), 1)

    def test_mesh_user_dm_round_trip(self):
        self.rig.guest.send_text("STRIKE", EDGE, 0)
        self.assertTrue(wait_for(lambda: self.rig.guest_inbox))
        self.assertIn("Bolt #1", self.rig.guest_inbox[0])
        self.assertEqual(len(self.rig.ha.calls), 1)

    def test_dm_flood_is_throttled(self):
        """F-06: menu spam gets a few answers, then silence."""
        for _ in range(40):
            self.rig.guest.send_text("?", EDGE, 0)
        self.rig.mesh.settle()
        time.sleep(0.5)
        self.rig.mesh.settle()
        self.assertLessEqual(len(self.rig.guest_inbox), 3)
        stats = self.rig.bbs.stats
        self.assertGreater(stats["dm_dropped"] + stats["replies_suppressed"], 30)

    def test_edge_ignores_forged_replies(self):
        """F-18: a reply signed with the wrong secret is ignored, so the guest isn't misled."""
        self.rig.mesh.drop = lambda src, dst, ch, text: src == PORCH
        original = self.rig.edge.send_text

        def send_and_forge(text, destination, channel, hop_limit=None):
            original(text, destination, channel, hop_limit)
            seq = int(text.split()[1])
            self.rig.stranger.send_text(P.Reply(seq, "OK", 999).encode(OTHER_KEY), BROADCAST, 1)

        self.rig.edge.send_text = send_and_forge
        r = self.rig.bbs.summon("web:a", "g", "STRIKE")
        self.assertEqual(r.status, "error")

    def test_store_logs_and_restores_last_summoner(self):
        store = Store(":memory:")
        self.rig.bbs.store = store
        self.rig.bbs.summon("web:a", "Morticia", "STRIKE")
        self.assertEqual(store.last(), ("Morticia", 1))

    def test_store_retention(self):
        store = Store(":memory:", retention_days=7)
        store._db.execute(
            "INSERT INTO summons (at, source, name, command, status, bolts) VALUES (0,'x','old','STRIKE','ok',1)"
        )
        store.log("web:a", "new", "STRIKE", "ok", 2)
        self.assertEqual(store.purge(), 1)
        self.assertEqual(store.last(), ("new", 2))


class Counters(unittest.TestCase):
    def test_send_counter_never_reuses_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "seq.json")
            c = SendCounter(path, block=10)
            used = [c.next() for _ in range(3)]
            c2 = SendCounter(path, block=10)  # crash and restart
            self.assertGreater(c2.next(), max(used))


if __name__ == "__main__":
    unittest.main()
