"""The Storm BBS: runs on the untrusted edge CM4.

It answers guests (web buttons and Meshtastic DMs), enforces cooldowns, and turns an
accepted summon into one signed ``SB2`` request on the private channel. It holds no Home
Assistant credentials and has no route to the home network: the only thing it can do
to the house is ask the translator, which checks the request's signature and sequence
number and decides for itself.

Abuse limits (see SECURITY-AUDIT.md):

* web and mesh summons have separate global budgets, so a radio flood with spoofed
  node IDs can't starve the guests at the gate, or the reverse (F-05);
* every reply to a mesh user is throttled per sender and overall, and at most
  ``max_pending_dms`` DMs wait for a worker; the rest are dropped silently (F-06).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .. import protocol as P
from ..config import EdgeConfig
from ..mesh import BROADCAST, MeshUnavailable, Transport, packet_channel, packet_from, packet_text, packet_to
from ..ratelimit import GuestLimiter
from ..seqstate import SendCounter
from .store import Store

log = logging.getLogger(__name__)

MAX_GUEST_TEXT = 40
MESH_VISITOR = "a mesh visitor"


@dataclass(frozen=True)
class Result:
    status: str  # ok | closed | busy | cooldown | error | info | unknown
    message: str
    bolts: int | None = None
    retry_after: int | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status, "message": self.message}
        if self.bolts is not None:
            d["bolts"] = self.bolts
        if self.retry_after is not None:
            d["retry_after"] = self.retry_after
        return d


MESSAGES = {
    "STRIKE": "Bolt #{n} summoned! Watch the sky.",
    "BOOM": "BOOM! Bolt #{n} - the big one.",
    "STORM": "The storm rises! (summon #{n})",
    "FIRE": "The fires awaken. (summon #{n})",
    "HEART": "Listen... the heart beats. (summon #{n})",
    "closed": "The storm sleeps. Come back during show hours.",
    "busy": "The sky is already rumbling. Try again in a few seconds.",
    "error": "The spirits didn't answer. Try again.",
    "cooldown": "The clouds are recharging. Try again in {s} s.",
}

MENU = "Storm Bot: reply 1 STRIKE, 2 BOOM, 3 STORM, 4 FIRE, 5 HEART, 6 WHO"


class _Pending:
    __slots__ = ("event", "reply")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.reply: P.Reply | None = None


class StormBBS:
    def __init__(
        self,
        cfg: EdgeConfig,
        transport: Transport,
        store: Store | None = None,
        clock: Callable[[], float] = time.monotonic,
        workers: int = 4,
        counter: SendCounter | None = None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport
        self.store = store
        self.clock = clock
        self.counter = counter or SendCounter(cfg.seq_path)
        now = clock()
        self.limiters = {
            "web": GuestLimiter(cfg.guest_cooldown_s, cfg.web_per_minute, cfg.web_burst, now),
            "mesh": GuestLimiter(cfg.guest_cooldown_s, cfg.mesh_per_minute, cfg.mesh_burst, now),
        }
        self.reply_limiter = GuestLimiter(
            cfg.mesh_reply_cooldown_s, cfg.mesh_replies_per_minute, 5, now, per_key_burst=3
        )
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bbs-dm")
        self._dm_backlog = 0
        self.stats = {"dm_dropped": 0, "replies_suppressed": 0, "bad_replies": 0}
        self.last_summoner: str | None = None
        self.bolts: int | None = None
        if store is not None:
            self.last_summoner, self.bolts = store.last()

    # ------------------------------------------------------------------ guests
    def summon(self, source: str, display_name: str, word: str) -> Result:
        """Handle one guest command. Blocks until the translator answers or times out.

        ``source`` identifies the guest for cooldowns: ``web:<mac or ip>`` or ``mesh:<id>``.
        """
        word = word.strip().upper()[:MAX_GUEST_TEXT]
        word = P.MENU_NUMBERS.get(word, word)
        if word in ("?", "HELP", "MENU", ""):
            return Result("info", MENU)
        if word in ("WHO", "#"):
            return Result("info", self._who())
        action = P.GUEST_ACTIONS.get(word)
        if action is None:
            return Result("unknown", "Unknown command. " + MENU)

        limiter = self.limiters["mesh" if source.startswith("mesh:") else "web"]
        wait = limiter.take(source, self.clock())
        if wait > 0:
            secs = max(1, round(wait))
            return Result("cooldown", MESSAGES["cooldown"].format(s=secs), retry_after=secs)

        verb, level = action
        reply = self._ask(verb, level)
        result = self._result_for(word, reply)
        if result.status != "ok":
            limiter.refund(source)  # nothing happened, don't charge the guest
        else:
            with self._lock:
                self.last_summoner = display_name
                self.bolts = result.bolts
        if self.store is not None:
            self.store.log(source, display_name, word, result.status, result.bolts)
        return result

    def _who(self) -> str:
        with self._lock:
            who, n = self.last_summoner, self.bolts
        if not who:
            return "No one has summoned the storm yet tonight..."
        return f"Last summoned by {who}. Summons tonight: {n if n is not None else '?'}"

    def _result_for(self, word: str, reply: P.Reply | None) -> Result:
        if reply is None:
            return Result("error", MESSAGES["error"])
        if reply.status == P.STATUS_OK:
            return Result("ok", MESSAGES[word].format(n=reply.bolts), bolts=reply.bolts)
        if reply.status == P.STATUS_CLOSED:
            return Result("closed", MESSAGES["closed"])
        if reply.status == P.STATUS_BUSY:
            return Result("busy", MESSAGES["busy"])
        return Result("error", MESSAGES["error"])

    # ------------------------------------------------------------------ translator link
    def _ask(self, verb: str, level: int | None, retry_stale: bool = True) -> P.Reply | None:
        try:
            seq = self.counter.next()
        except (OSError, RuntimeError):
            log.exception("cannot allocate a sequence number")
            return None
        pending = _Pending()
        with self._lock:
            self._pending[seq] = pending
        try:
            text = P.Request(seq, verb, level).encode(self.cfg.link_key)
            try:
                self.transport.send_text(text, BROADCAST, self.cfg.radio.private_channel, self.cfg.hop_limit)
            except MeshUnavailable:
                log.warning("radio down; cannot send %s %s", seq, verb)
                return None
            log.info("sent seq %d %s %s", seq, verb, "" if level is None else level)
            if not pending.event.wait(self.cfg.reply_timeout_s):
                log.warning("no reply to seq %d within %.0f s", seq, self.cfg.reply_timeout_s)
                return None
            reply = pending.reply
        finally:
            with self._lock:
                self._pending.pop(seq, None)
        if reply is not None and reply.status == P.STATUS_STALE:
            # Authentic STALE: our counter is behind (e.g. this box was re-imaged).
            self.counter.jump_past(reply.value or 0)
            return self._ask(verb, level, retry_stale=False) if retry_stale else None
        return reply

    def ping(self) -> P.Reply | None:
        """Round-trip check to the translator (tests T3/T4). Takes no action."""
        return self._ask("PING", None)

    # ------------------------------------------------------------------ radio input
    def on_packet(self, packet: dict[str, Any]) -> None:
        """Called on the radio reader thread. Must not block."""
        text = packet_text(packet)
        sender = packet_from(packet)
        if text is None or sender is None:
            return
        channel = packet_channel(packet)

        if channel == self.cfg.radio.private_channel:
            if sender != self.cfg.porch_node_id:
                log.warning("ignoring private-channel text from unexpected node %s", sender)
                return
            reply = P.parse_reply(text, self.cfg.link_key)
            if reply is None:
                if text.startswith("SB2 "):
                    self.stats["bad_replies"] += 1
                    log.warning("dropped a reply that failed its signature check")
                return
            with self._lock:
                pending = self._pending.get(reply.seq)
            if pending is None:
                log.info("late or unknown reply for seq %d", reply.seq)
                return
            pending.reply = reply
            pending.event.set()
            return

        # Anything else: only direct messages to us are guest commands.
        me = self.transport.my_node_id()
        if me is None or packet_to(packet) != me:
            return
        with self._lock:
            if self._dm_backlog >= self.cfg.max_pending_dms:
                self.stats["dm_dropped"] += 1
                return
            self._dm_backlog += 1
        self._pool.submit(self._answer_dm, sender, channel, text)

    def _answer_dm(self, sender: str, channel: int, text: str) -> None:
        try:
            if self.reply_limiter.take(f"mesh:{sender}", self.clock()) > 0:
                self.stats["replies_suppressed"] += 1
                return  # over the reply budget: stay silent, spend no airtime
            if self.cfg.show_mesh_names:
                name = self.transport.node_name(sender) or MESH_VISITOR
            else:
                name = MESH_VISITOR
            result = self.summon(f"mesh:{sender}", name, text)
            try:
                self.transport.send_text(result.message, sender, channel)
            except MeshUnavailable:
                log.warning("radio down; could not answer %s", sender)
        finally:
            with self._lock:
                self._dm_backlog -= 1

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
