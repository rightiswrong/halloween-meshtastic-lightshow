"""The Storm BBS: runs on the untrusted edge CM4.

It answers guests (web buttons and Meshtastic DMs), enforces cooldowns, and turns an
accepted summon into one ``SB1`` request on the private channel. It holds no Home
Assistant credentials and has no route to the home network: the only thing it can do
to the house is ask the translator, which decides for itself.
"""

from __future__ import annotations

import logging
import secrets
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
from .store import Store

log = logging.getLogger(__name__)

MAX_GUEST_TEXT = 40


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
    ) -> None:
        self.cfg = cfg
        self.transport = transport
        self.store = store
        self.clock = clock
        self.limiter = GuestLimiter(cfg.guest_cooldown_s, cfg.global_per_minute, cfg.global_burst, clock())
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bbs-dm")
        self.last_summoner: str | None = None
        self.bolts: int | None = None
        if store is not None:
            self.last_summoner, self.bolts = store.last()

    # ------------------------------------------------------------------ guests
    def summon(self, source: str, display_name: str, word: str) -> Result:
        """Handle one guest command. Blocks until the translator answers or times out.

        ``source`` identifies the guest for cooldowns (``web:<ip>`` or ``mesh:<id>``).
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

        wait = self.limiter.take(source, self.clock())
        if wait > 0:
            secs = max(1, round(wait))
            return Result("cooldown", MESSAGES["cooldown"].format(s=secs), retry_after=secs)

        verb, level = action
        reply = self._ask(verb, level)
        result = self._result_for(word, reply)
        if result.status != "ok":
            self.limiter.refund(source)  # nothing happened, don't charge the guest
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
    def _new_rid(self) -> str:
        while True:
            rid = secrets.token_hex(2)
            if rid not in self._pending:
                return rid

    def _ask(self, verb: str, level: int | None) -> P.Reply | None:
        with self._lock:
            rid = self._new_rid()
            pending = self._pending[rid] = _Pending()
        try:
            text = P.Request(rid, verb, level).encode()
            try:
                self.transport.send_text(text, BROADCAST, self.cfg.radio.private_channel)
            except MeshUnavailable:
                log.warning("radio down; cannot send %s", text)
                return None
            log.info("sent %s", text)
            if not pending.event.wait(self.cfg.reply_timeout_s):
                log.warning("no reply to %s within %.0f s", text, self.cfg.reply_timeout_s)
                return None
            return pending.reply
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    def ping(self) -> P.Reply | None:
        """Round-trip check to the translator (test T3/T4). Takes no action."""
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
            reply = P.parse_reply(text)
            if reply is None:
                return  # our own request echoed by a relay, or noise
            with self._lock:
                pending = self._pending.get(reply.rid)
            if pending is None:
                log.info("late or unknown reply %s", text)
                return
            pending.reply = reply
            pending.event.set()
            return

        # Anything else: only direct messages to us are guest commands.
        me = self.transport.my_node_id()
        if me is None or packet_to(packet) != me:
            return
        self._pool.submit(self._answer_dm, sender, channel, text)

    def _answer_dm(self, sender: str, channel: int, text: str) -> None:
        name = self.transport.node_name(sender) or sender
        result = self.summon(f"mesh:{sender}", name, text)
        try:
            self.transport.send_text(result.message, sender, channel)
        except MeshUnavailable:
            log.warning("radio down; could not answer %s", sender)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
