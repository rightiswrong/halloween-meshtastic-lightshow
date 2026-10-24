"""Edge BBS <-> simulated LoRa <-> translator <-> fake HA (tests T4 and T7 without radios)."""

import unittest

from stormshow.bbs.store import Store
from stormshow.mesh import BROADCAST
from tests.helpers import EDGE, PORCH, Rig


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def tearDown(self):
        self.rig.close()

    def test_web_summon_reaches_home_assistant(self):
        r = self.rig.bbs.summon("web:10.66.0.20", "a guest", "strike")
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.bolts, 1)
        self.assertEqual(self.rig.ha.calls, [("script.storm_strike", {"strength": 0.8})])
        r = self.rig.bbs.summon("web:10.66.0.21", "a guest", "BOOM")
        self.assertEqual(r.bolts, 2)
        self.assertEqual(self.rig.ha.calls[-1], ("script.storm_strike", {"strength": 1.0}))

    def test_every_guest_action_maps_to_a_script(self):
        for i, word in enumerate(("STORM", "FIRE", "HEART")):
            r = self.rig.bbs.summon(f"web:10.66.0.{i}", "g", word)
            self.assertEqual(r.status, "ok", word)
        self.assertEqual(
            [c[0] for c in self.rig.ha.calls], ["script.storm_storm", "script.storm_fire", "script.storm_heart"]
        )

    def test_cooldown_per_guest_and_refund_on_failure(self):
        self.assertEqual(self.rig.bbs.summon("web:a", "g", "STRIKE").status, "ok")
        r = self.rig.bbs.summon("web:a", "g", "STRIKE")
        self.assertEqual(r.status, "cooldown")
        self.assertEqual(r.retry_after, 30)
        self.rig.ha.states["input_boolean.storm_show_running"] = "off"
        self.assertEqual(self.rig.bbs.summon("web:b", "g", "STRIKE").status, "closed")
        self.rig.ha.states["input_boolean.storm_show_running"] = "on"
        self.assertEqual(self.rig.bbs.summon("web:b", "g", "STRIKE").status, "ok")  # not charged

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

    def test_menu_who_and_unknown(self):
        self.assertIn("STRIKE", self.rig.bbs.summon("web:a", "g", "?").message)
        self.assertIn("No one", self.rig.bbs.summon("web:a", "g", "WHO").message)
        self.rig.bbs.summon("mesh:!00006e57", "Trick or Treater", "1")  # menu number for STRIKE
        self.assertIn("Trick or Treater", self.rig.bbs.summon("web:a", "g", "who").message)
        self.assertEqual(self.rig.bbs.summon("web:a", "g", "rm -rf /").status, "unknown")
        self.assertEqual(len(self.rig.ha.calls), 1)

    def test_mesh_user_dm_round_trip(self):
        self.rig.guest.send_text("STRIKE", EDGE, 0)
        for _ in range(50):
            self.rig.mesh.settle()
            if self.rig.guest_inbox:
                break
            import time

            time.sleep(0.02)
        self.assertEqual(len(self.rig.guest_inbox), 1)
        self.assertIn("Bolt #1", self.rig.guest_inbox[0])
        self.assertEqual(len(self.rig.ha.calls), 1)

    def test_edge_ignores_forged_replies(self):
        # A stranger with the PSK answers before the porch: the edge must not believe it.
        self.rig.mesh.drop = lambda src, dst, ch, text: src == PORCH
        original = self.rig.edge.send_text

        def send_and_forge(text, destination, channel):
            original(text, destination, channel)
            rid = text.split()[1]
            self.rig.stranger.send_text(f"SB1 {rid} OK 999", BROADCAST, 1)

        self.rig.edge.send_text = send_and_forge
        r = self.rig.bbs.summon("web:a", "g", "STRIKE")
        self.assertEqual(r.status, "error")

    def test_store_logs_and_restores_last_summoner(self):
        store = Store(":memory:")
        self.rig.bbs.store = store
        self.rig.bbs.summon("web:a", "Morticia", "STRIKE")
        self.assertEqual(store.last(), ("Morticia", 1))


if __name__ == "__main__":
    unittest.main()
