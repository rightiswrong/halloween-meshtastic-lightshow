"""Shared fixtures: a loopback mesh with an edge BBS, a porch translator and a fake HA."""

from __future__ import annotations

from datetime import datetime

from stormshow.bbs.service import StormBBS
from stormshow.config import EdgeConfig, RadioConfig, TranslatorConfig
from stormshow.loopback import FakeHomeAssistant, LoopbackMesh
from stormshow.protocol import LinkKey
from stormshow.translator.service import BoltCounter, Translator

EDGE = "!0000ed9e"
PORCH = "!0000beef"
GUEST = "!00006e57"
STRANGER = "!badc0ffe"
KEY = LinkKey(bytes(range(32)))
OTHER_KEY = LinkKey(bytes(range(1, 33)))


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def edge_config(**kw) -> EdgeConfig:
    base = dict(
        radio=RadioConfig(port="loopback", private_channel=1),
        porch_node_id=PORCH,
        link_key=KEY,
        reply_timeout_s=2,
        guest_cooldown_s=30,
        web_per_minute=60,
        web_burst=10,
        mesh_per_minute=60,
        mesh_burst=10,
        listen="127.0.0.1",
        web_port=0,
        portal_url="http://portal.test/",
        dhcp_leases=None,
    )
    base.update(kw)
    return EdgeConfig(**base)


def translator_config(**kw) -> TranslatorConfig:
    base = dict(
        radio=RadioConfig(host="loopback", private_channel=1),
        edge_node_ids=[EDGE],
        link_key=KEY,
        ha_url="http://fake",
        ha_mode="token",
        ha_token="fake",
        show_entity="input_boolean.storm_show_running",
        scripts={v: f"script.storm_{v.lower()}" for v in ("STRIKE", "STORM", "FIRE", "HEART")},
        per_minute=60,
        burst=5,
    )
    base.update(kw)
    return TranslatorConfig(**base)


class Rig:
    """Edge + porch + guest + stranger radios on one simulated mesh."""

    def __init__(self, edge_kw=None, translator_kw=None) -> None:
        self.mesh = LoopbackMesh()
        self.edge = self.mesh.node(EDGE, "Storm Station")
        self.porch = self.mesh.node(PORCH, "Porch")
        self.guest = self.mesh.node(GUEST, "Trick or Treater", channels=(0,))
        self.stranger = self.mesh.node(STRANGER, "Stranger")  # somehow has the private PSK
        self.ha = FakeHomeAssistant()
        self.ha.states["input_boolean.storm_show_running"] = "on"
        self.edge_clock = FakeClock()
        self.tr_clock = FakeClock()
        self.translator = Translator(
            translator_config(**(translator_kw or {})),
            self.ha,
            self.porch,
            BoltCounter(None, 12, now=lambda: datetime(2026, 10, 31, 20, 0)),
            clock=self.tr_clock,
        )
        self.porch.handler = self.translator.on_packet
        self.bbs = StormBBS(edge_config(**(edge_kw or {})), self.edge, clock=self.edge_clock)
        self.edge.handler = self.bbs.on_packet
        self.guest_inbox: list[str] = []
        self.guest.handler = lambda p: self.guest_inbox.append(p["decoded"]["text"])

    def close(self) -> None:
        self.bbs.close()
        self.translator.close()
