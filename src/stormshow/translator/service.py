"""The translator: runs on the trusted LAN (a Proxmox VM next to Home Assistant).

It listens to the porch Meshtastic node and acts only on messages that

1. arrive on the private channel (whose PSK only the edge and porch radios hold),
2. come from an allowlisted edge node ID, and
3. match the ``SB1`` request grammar exactly.

Each accepted request becomes one ``script.turn_on`` call in Home Assistant. The
translator never forwards text, never runs anything else, and rate-limits itself, so
even a compromised edge station can only do what a guest can.

Note: Home Assistant has no per-service permissions for API tokens. The allowlist
here, not the token, is what limits what the mesh can do. Keep the token on this VM.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import protocol as P
from ..config import TranslatorConfig
from ..mesh import BROADCAST, MeshUnavailable, Transport, packet_channel, packet_from, packet_text
from ..ratelimit import TokenBucket
from .ha import HAError, HomeAssistant

log = logging.getLogger(__name__)

DEDUP_WINDOW_S = 120.0


class BoltCounter:
    """Summons per night. A "night" runs from ``reset_hour`` local time to the next."""

    def __init__(self, path: str | None, reset_hour: int, now: Callable[[], datetime] = datetime.now) -> None:
        self._path = Path(path) if path else None
        self._reset_hour = reset_hour
        self._now = now
        self._night = self._night_key()
        self._count = 0
        self._lock = threading.Lock()
        if self._path and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf-8"))
                if saved.get("night") == self._night:
                    self._count = int(saved.get("bolts", 0))
            except (ValueError, OSError):
                log.warning("could not read %s; starting the count at 0", self._path)

    def _night_key(self) -> str:
        return (self._now() - timedelta(hours=self._reset_hour)).date().isoformat()

    def increment(self) -> int:
        with self._lock:
            night = self._night_key()
            if night != self._night:
                self._night, self._count = night, 0
            self._count += 1
            self._save()
            return self._count

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"night": self._night, "bolts": self._count}), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            log.exception("could not save the bolt count")


class Translator:
    def __init__(
        self,
        cfg: TranslatorConfig,
        ha: HomeAssistant,
        transport: Transport,
        counter: BoltCounter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.ha = ha
        self.transport = transport
        self.counter = counter or BoltCounter(cfg.state_path, cfg.reset_hour)
        self.clock = clock
        self._allowed = frozenset(cfg.edge_node_ids)
        self._bucket = TokenBucket(cfg.per_minute / 60.0, cfg.burst, clock())
        self._recent: dict[str, tuple[float, str | None]] = {}  # rid -> (when, reply text)
        self._lock = threading.Lock()
        # One worker: Home Assistant calls run in arrival order, never in parallel.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translator")
        self.stats = {"accepted": 0, "dropped": 0, "busy": 0, "errors": 0}

    # ------------------------------------------------------------------ radio input
    def on_packet(self, packet: dict[str, Any]) -> None:
        """Called on the radio reader thread. Cheap checks only; HA work goes to the pool."""
        if packet_channel(packet) != self.cfg.radio.private_channel:
            return  # public chatter: not for us, not worth a log line
        text = packet_text(packet)
        sender = packet_from(packet)
        if text is None:
            return
        if sender not in self._allowed:
            self._drop(f"sender {sender} not allowlisted", text)
            return
        req = P.parse_request(text)
        if req is None:
            self._drop(f"not a valid request from {sender}", text)
            return

        now = self.clock()
        with self._lock:
            self._recent = {r: v for r, v in self._recent.items() if now - v[0] < DEDUP_WINDOW_S}
            seen = self._recent.get(req.rid)
            if seen is not None:
                if seen[1] is not None:  # already answered: the edge missed our reply
                    log.info("duplicate %s; resending %s", text, seen[1])
                    self._pool.submit(self._send, seen[1])
                return  # still processing: ignore the duplicate
            self._recent[req.rid] = (now, None)

        if req.verb == "PING":
            self._pool.submit(self._finish, req, P.Reply(req.rid, P.STATUS_PONG))
            return
        with self._lock:
            allowed = self._bucket.take(now)
        if not allowed:
            self.stats["busy"] += 1
            self._pool.submit(self._finish, req, P.Reply(req.rid, P.STATUS_BUSY))
            return
        self.stats["accepted"] += 1
        log.info("accepted %s from %s", text, sender)
        self._pool.submit(self._process, req)

    def _drop(self, why: str, text: str) -> None:
        self.stats["dropped"] += 1
        log.warning("dropped (%s): %r", why, text[:60])

    # ------------------------------------------------------------------ actions
    def _process(self, req: P.Request) -> None:
        try:
            reply = self.execute(req)
        except Exception:  # noqa: BLE001 - always answer the edge
            log.exception("unexpected error running %s", req)
            reply = P.Reply(req.rid, P.STATUS_ERR)
        self._finish(req, reply)

    def execute(self, req: P.Request) -> P.Reply:
        """Run one accepted request against Home Assistant and build the reply."""
        try:
            if self.cfg.show_entity:
                state = self.ha.state(self.cfg.show_entity)
                if state != "on":
                    log.info("show is closed (%s = %s)", self.cfg.show_entity, state)
                    return P.Reply(req.rid, P.STATUS_CLOSED)
            variables = {"strength": round(req.level / 100, 2)} if req.level is not None else None
            self.ha.run_script(self.cfg.scripts[req.verb], variables)
        except HAError as err:
            self.stats["errors"] += 1
            log.error("%s failed: %s", req.verb, err)
            return P.Reply(req.rid, P.STATUS_ERR)
        return P.Reply(req.rid, P.STATUS_OK, self.counter.increment())

    def _finish(self, req: P.Request, reply: P.Reply) -> None:
        text = reply.encode()
        with self._lock:
            self._recent[req.rid] = (self.clock(), text)
        self._send(text)

    def _send(self, text: str) -> None:
        try:
            self.transport.send_text(text, BROADCAST, self.cfg.radio.private_channel)
            log.info("replied %s", text)
        except MeshUnavailable:
            log.warning("radio down; reply %s not sent", text)

    def drain(self, timeout: float = 5.0) -> None:
        """Wait for queued work (tests)."""
        self._pool.submit(lambda: None).result(timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
