"""Thin, reconnecting wrapper around the ``meshtastic`` Python library.

The services never import ``meshtastic`` themselves; they talk to a ``Transport``.
That keeps them testable without radios (see ``stormshow.loopback``) and keeps the
library's quirks in one place.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from .config import RadioConfig

log = logging.getLogger(__name__)

BROADCAST = "^all"


class MeshUnavailable(RuntimeError):
    """The radio is not connected right now."""


class Transport(Protocol):
    def my_node_id(self) -> str | None: ...
    def node_name(self, node_id: str) -> str | None: ...
    def send_text(self, text: str, destination: str, channel: int) -> None: ...


PacketHandler = Callable[[dict[str, Any]], None]


class MeshLink:
    """Owns one Meshtastic API connection and reconnects when it drops.

    ``on_text(packet)`` is called on the library's reader thread for every received
    text message. Handlers must return quickly: anything slow (waiting for a reply,
    calling Home Assistant) belongs on a worker thread, or replies can't be read.
    """

    def __init__(self, radio: RadioConfig, on_text: PacketHandler, retry_s: float = 10.0) -> None:
        self._radio = radio
        self._on_text = on_text
        self._retry_s = retry_s
        self._iface: Any = None
        self._lost = threading.Event()
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        from pubsub import pub  # installed with meshtastic

        pub.subscribe(self._handle_text, "meshtastic.receive.text")
        pub.subscribe(self._handle_lost, "meshtastic.connection.lost")
        self._thread = threading.Thread(target=self._run, name="meshlink", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._lost.set()
        if self._thread:
            self._thread.join(timeout=5)

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready.wait(timeout)

    def _connect(self) -> Any:
        if self._radio.port:
            from meshtastic.serial_interface import SerialInterface

            log.info("connecting to Meshtastic node on serial %s", self._radio.port)
            return SerialInterface(devPath=self._radio.port)
        from meshtastic.tcp_interface import TCPInterface

        log.info("connecting to Meshtastic node at tcp://%s:4403", self._radio.host)
        return TCPInterface(hostname=self._radio.host)

    def _run(self) -> None:
        while not self._stop.is_set():
            iface = None
            try:
                self._lost.clear()
                iface = self._connect()
                self._iface = iface
                self._ready.set()
                log.info("radio connected as %s", self.my_node_id())
                self._lost.wait()
                if not self._stop.is_set():
                    log.warning("radio connection lost; reconnecting")
            except Exception:  # noqa: BLE001 - keep the service alive whatever the library throws
                log.exception("radio connection failed; retrying in %.0f s", self._retry_s)
            finally:
                self._ready.clear()
                self._iface = None
                if iface is not None:
                    try:
                        iface.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._stop.wait(self._retry_s)

    # ------------------------------------------------------------------ pubsub
    def _handle_text(self, packet: dict[str, Any], interface: Any = None) -> None:
        if interface is not None and interface is not self._iface:
            return
        try:
            self._on_text(packet)
        except Exception:  # noqa: BLE001 - never let a bad packet kill the reader thread
            log.exception("error handling packet")

    def _handle_lost(self, interface: Any = None) -> None:
        if interface is None or interface is self._iface:
            self._lost.set()

    # ------------------------------------------------------------------ Transport
    def my_node_id(self) -> str | None:
        iface = self._iface
        info = getattr(iface, "myInfo", None)
        num = getattr(info, "my_node_num", None)
        return f"!{num:08x}" if isinstance(num, int) else None

    def node_name(self, node_id: str) -> str | None:
        iface = self._iface
        nodes = getattr(iface, "nodes", None) or {}
        user = (nodes.get(node_id) or {}).get("user") or {}
        return user.get("longName") or user.get("shortName")

    def send_text(self, text: str, destination: str, channel: int) -> None:
        iface = self._iface
        if iface is None:
            raise MeshUnavailable("radio not connected")
        with self._send_lock:
            iface.sendText(
                text,
                destinationId=destination,
                wantAck=destination != BROADCAST,
                channelIndex=channel,
            )


def packet_text(packet: dict[str, Any]) -> str | None:
    """The text of a TEXT_MESSAGE_APP packet, or None."""
    decoded = packet.get("decoded") or {}
    text = decoded.get("text")
    return text if isinstance(text, str) else None


def packet_from(packet: dict[str, Any]) -> str | None:
    """Sender node ID like '!a1b2c3d4', from the numeric field (fromId can be missing)."""
    num = packet.get("from")
    if isinstance(num, int) and 0 <= num <= 0xFFFFFFFF:
        return f"!{num:08x}"
    sid = packet.get("fromId")
    return sid.lower() if isinstance(sid, str) else None


def packet_to(packet: dict[str, Any]) -> str | None:
    """Destination node ID, or BROADCAST."""
    num = packet.get("to")
    if num == 0xFFFFFFFF:
        return BROADCAST
    if isinstance(num, int) and 0 <= num < 0xFFFFFFFF:
        return f"!{num:08x}"
    tid = packet.get("toId")
    return tid.lower() if isinstance(tid, str) else None


def packet_channel(packet: dict[str, Any]) -> int:
    """Channel index the packet arrived on (the library omits 0)."""
    ch = packet.get("channel", 0)
    return ch if isinstance(ch, int) else -1
