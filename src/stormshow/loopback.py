"""An in-memory mesh for tests and the ``--demo`` mode (no radios needed).

Packets are delivered on a background thread, like a real radio, as dictionaries
shaped like the ``meshtastic`` library's: ``from``/``to`` node numbers, ``channel``
(omitted for 0) and ``decoded.text``.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from .mesh import BROADCAST, MeshUnavailable

log = logging.getLogger(__name__)

BROADCAST_NUM = 0xFFFFFFFF


class LoopbackMesh:
    def __init__(self) -> None:
        self._nodes: dict[int, LoopbackNode] = {}
        self._queue: queue.Queue = queue.Queue()
        self.sent: list[tuple[str, str, int, str]] = []  # (from, to, channel, text)
        self.drop: Callable[[str, str, int, str], bool] = lambda *_: False  # simulate loss
        self._thread = threading.Thread(target=self._deliver, name="loopback", daemon=True)
        self._thread.start()

    def node(self, node_id: str, name: str = "", channels: tuple[int, ...] = (0, 1)) -> LoopbackNode:
        num = int(node_id.lstrip("!"), 16)
        n = LoopbackNode(self, num, name or node_id, set(channels))
        self._nodes[num] = n
        return n

    def _send(self, src: LoopbackNode, text: str, destination: str, channel: int) -> None:
        if not src.up:
            raise MeshUnavailable("node is down")
        if channel not in src.channels:
            raise ValueError(f"node has no channel {channel}")
        self.sent.append((src.node_id, destination, channel, text))
        if self.drop(src.node_id, destination, channel, text):
            return
        to = BROADCAST_NUM if destination == BROADCAST else int(destination.lstrip("!"), 16)
        packet: dict[str, Any] = {"from": src.num, "to": to, "decoded": {"text": text}}
        if channel:
            packet["channel"] = channel
        self._queue.put((src.num, to, channel, packet))

    def _deliver(self) -> None:
        while True:
            src, to, channel, packet = self._queue.get()
            for num, node in list(self._nodes.items()):
                if num == src or channel not in node.channels or not node.up:
                    continue
                if to != BROADCAST_NUM and to != num:
                    continue
                if node.handler:
                    try:
                        node.handler(dict(packet))
                    except Exception:  # noqa: BLE001
                        log.exception("loopback handler failed")
            self._queue.task_done()

    def settle(self) -> None:
        """Block until every queued packet has been delivered."""
        self._queue.join()


class LoopbackNode:
    """Implements the ``Transport`` protocol for one simulated radio."""

    def __init__(self, mesh: LoopbackMesh, num: int, name: str, channels: set[int]) -> None:
        self.mesh = mesh
        self.num = num
        self.name = name
        self.channels = channels
        self.handler: Callable[[dict[str, Any]], None] | None = None
        self.up = True

    @property
    def node_id(self) -> str:
        return f"!{self.num:08x}"

    def my_node_id(self) -> str | None:
        return self.node_id

    def node_name(self, node_id: str) -> str | None:
        node = self.mesh._nodes.get(int(node_id.lstrip("!"), 16))
        return node.name if node else None

    def send_text(self, text: str, destination: str, channel: int) -> None:
        self.mesh._send(self, text, destination, channel)


class FakeHomeAssistant:
    """Stands in for ``HomeAssistant``: records calls, can be closed or broken."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.states: dict[str, str] = {}
        self.fail = False

    def check(self) -> str:
        return "API running. (fake)"

    def state(self, entity_id: str) -> str | None:
        from .translator.ha import HAError

        if self.fail:
            raise HAError("fake outage")
        return self.states.get(entity_id)

    def run_script(self, entity_id: str, variables: dict | None = None) -> None:
        from .translator.ha import HAError

        if self.fail:
            raise HAError("fake outage")
        log.info("[fake HA] %s %s", entity_id, variables or "")
        self.calls.append((entity_id, variables))
