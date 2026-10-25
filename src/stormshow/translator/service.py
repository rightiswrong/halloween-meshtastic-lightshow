"""The translator: runs on the trusted LAN (a Raspberry Pi on the Home Assistant subnet).

It listens to the porch Meshtastic node and acts only on messages that

1. arrive on the private channel,
2. come from an allowlisted edge node ID,
3. match the ``SB2`` request grammar exactly, **and carry a valid HMAC tag** made with
   the link secret (the channel key alone is not enough: SECURITY-AUDIT.md F-01), and
4. have a sequence number it has not accepted before (replays are refused).

Each accepted request becomes one Home Assistant call: by default a webhook that can
only run the four Halloween effects (F-02). The translator never forwards text and
rate-limits itself, including its own replies, so even a compromised edge station can
only do what a guest can (F-15).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import protocol as P
from ..config import TranslatorConfig
from ..mesh import BROADCAST, MeshUnavailable, Transport, packet_channel, packet_from, packet_text
from ..ratelimit import TokenBucket
from ..seqstate import ReceiveWindow
from .ha import HAError

log = logging.getLogger(__name__)

REPLY_CACHE = 128


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


def in_show_hours(hours: tuple | None, now: datetime) -> bool:
    """True if ``now`` is inside 'start-end' local time; handles shows that run past midnight."""
    if hours is None:
        return True
    start, end = hours
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


class Translator:
    def __init__(
        self,
        cfg: TranslatorConfig,
        ha: Any,
        transport: Transport,
        counter: BoltCounter | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
        window: ReceiveWindow | None = None,
    ) -> None:
        self.cfg = cfg
        self.ha = ha
        self.transport = transport
        self.counter = counter or BoltCounter(cfg.state_path, cfg.reset_hour)
        self.window = window or ReceiveWindow(cfg.link_state_path)
        self.clock = clock
        self.wall_clock = wall_clock
        self._key = cfg.link_key
        self._allowed = frozenset(cfg.edge_node_ids)
        now = clock()
        self._bucket = TokenBucket(cfg.per_minute / 60.0, cfg.burst, now)
        self._ping_bucket = TokenBucket(cfg.ping_per_minute / 60.0, 2, now)
        self._courtesy = TokenBucket(1.0, 2, now)  # BUSY/STALE/resent replies: ~1 per second
        self._answered: OrderedDict[int, str] = OrderedDict()  # seq -> reply text
        self._lock = threading.Lock()
        self._queued = 0
        # One worker: Home Assistant calls run in arrival order, never in parallel.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translator")
        self.stats = {"accepted": 0, "dropped": 0, "busy": 0, "errors": 0, "replayed": 0, "overflow": 0}

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
        req = P.parse_request(text, self._key)
        if req is None:
            self._drop(f"bad syntax or signature from {sender}", text)
            return

        now = self.clock()
        with self._lock:
            if not self.window.is_fresh(req.seq):
                self.stats["replayed"] += 1
                cached = self._answered.get(req.seq)
                if not self._courtesy.take(now):
                    return  # replies to replays are rate-limited too (F-15)
                if cached is not None:  # the edge missed our reply: resend it, run nothing
                    log.info("duplicate seq %d; resending the reply", req.seq)
                    self._enqueue(self._send, cached)
                elif req.seq <= self.window.highest - self.window.size:
                    # Older than the window: a replay, or an edge that lost its counter.
                    log.warning("stale or replayed seq %d (highest %d)", req.seq, self.window.highest)
                    self._enqueue(self._finish, req, P.Reply(req.seq, P.STATUS_STALE, self.window.highest))
                return  # otherwise: a duplicate of a request still being processed
            self.window.accept(req.seq)

            if req.verb == "PING":
                if self._ping_bucket.take(now):
                    self._enqueue(self._finish, req, P.Reply(req.seq, P.STATUS_PONG))
                return
            if not self._bucket.take(now):
                self.stats["busy"] += 1
                if self._courtesy.take(now):
                    self._enqueue(self._finish, req, P.Reply(req.seq, P.STATUS_BUSY))
                return
            self.stats["accepted"] += 1
            log.info("accepted seq %d %s %s", req.seq, req.verb, "" if req.level is None else req.level)
            self._enqueue(self._process, req)

    def _enqueue(self, fn: Callable, *args: Any) -> None:
        """Queue work, but never more than ``max_queue`` items (call with the lock held)."""
        if self._queued >= self.cfg.max_queue:
            self.stats["overflow"] += 1
            return

        def run() -> None:
            try:
                fn(*args)
            finally:
                with self._lock:
                    self._queued -= 1

        self._queued += 1
        self._pool.submit(run)

    def _drop(self, why: str, text: str) -> None:
        self.stats["dropped"] += 1
        log.warning("dropped (%s): %r", why, text[:60])

    # ------------------------------------------------------------------ actions
    def _process(self, req: P.Request) -> None:
        try:
            reply = self.execute(req)
        except Exception:  # noqa: BLE001 - always answer the edge
            log.exception("unexpected error running %s", req)
            reply = P.Reply(req.seq, P.STATUS_ERR)
        self._finish(req, reply)

    def execute(self, req: P.Request) -> P.Reply:
        """Run one accepted request against Home Assistant and build the reply."""
        if not in_show_hours(self.cfg.show_hours, self.wall_clock()):
            log.info("outside show hours")
            return P.Reply(req.seq, P.STATUS_CLOSED)
        try:
            if self.cfg.show_entity and getattr(self.ha, "has_state", False):
                state = self.ha.state(self.cfg.show_entity)
                if state != "on":
                    log.info("show is closed (%s = %s)", self.cfg.show_entity, state)
                    return P.Reply(req.seq, P.STATUS_CLOSED)
            strength = round(req.level / 100, 2) if req.level is not None else None
            self.ha.trigger(req.verb, strength)
        except HAError as err:
            self.stats["errors"] += 1
            log.error("%s failed: %s", req.verb, err)
            return P.Reply(req.seq, P.STATUS_ERR)
        return P.Reply(req.seq, P.STATUS_OK, self.counter.increment())

    def _finish(self, req: P.Request, reply: P.Reply) -> None:
        text = reply.encode(self._key)
        with self._lock:
            self._answered[req.seq] = text
            while len(self._answered) > REPLY_CACHE:
                self._answered.popitem(last=False)
        self._send(text)

    def _send(self, text: str) -> None:
        try:
            self.transport.send_text(text, BROADCAST, self.cfg.radio.private_channel, self.cfg.hop_limit)
            log.info("replied %s", text.rsplit(" ", 1)[0])
        except MeshUnavailable:
            log.warning("radio down; reply not sent")

    def drain(self, timeout: float = 5.0) -> None:
        """Wait for queued work (tests)."""
        self._pool.submit(lambda: None).result(timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
