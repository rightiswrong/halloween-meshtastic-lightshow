"""The signed wire protocol between the edge BBS and the translator (``SB2``).

Everything that crosses the trust boundary is one short line of ASCII on the private
Meshtastic channel. Meshtastic's channel encryption (AES-CTR) hides the text but does
not authenticate it: a packet can be replayed, or bit-flipped into a different command,
without the channel key (SECURITY-AUDIT.md, F-01). So every line carries its own

* sequence number ``seq``: strictly increasing per sender, so the receiver can reject
  anything old or repeated (see ``ReplayWindow``);
* HMAC-SHA256 tag over the version, direction, seq and fields, keyed with a 32-byte
  link secret that is *not* the channel PSK, truncated to 64 bits and base64url-encoded
  (11 characters). Without the secret, a valid tag can't be made and any change to the
  line is detected.

Request (edge -> translator), broadcast on the private channel::

    SB2 <seq> STRIKE <0..100> <tag>
    SB2 <seq> STORM <tag>          also FIRE, HEART, PING

Reply (translator -> edge), echoing the request's seq::

    SB2 <seq> OK <bolts> <tag>     the action ran; <bolts> = summons tonight
    SB2 <seq> CLOSED <tag>         outside show hours
    SB2 <seq> BUSY <tag>           translator rate limit hit
    SB2 <seq> ERR <tag>            Home Assistant refused or was unreachable
    SB2 <seq> PONG <tag>           reply to PING (no action taken)
    SB2 <seq> STALE <highest> <tag>  seq was too old (e.g. the edge lost its counter);
                                   the edge jumps past <highest> and retries once

Requests and replies are tagged with different direction labels, so a reply can never
be replayed as a request (or the reverse). Every line fits Meshtastic's text limit with
plenty of room (at most 48 characters).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass

PREFIX = "SB2"
MAX_LEN = 48
SEQ_MAX = 4_294_967_295  # 2**32 - 1
TAG_CHARS = 11  # 8 bytes, base64url without padding
SECRET_BYTES = 32

VERBS_WITH_LEVEL = frozenset({"STRIKE"})
VERBS_PLAIN = frozenset({"STORM", "FIRE", "HEART", "PING"})
VERBS = VERBS_WITH_LEVEL | VERBS_PLAIN

STATUS_OK = "OK"
STATUS_CLOSED = "CLOSED"
STATUS_BUSY = "BUSY"
STATUS_ERR = "ERR"
STATUS_PONG = "PONG"
STATUS_STALE = "STALE"
STATUSES = frozenset({STATUS_OK, STATUS_CLOSED, STATUS_BUSY, STATUS_ERR, STATUS_PONG, STATUS_STALE})

_SEQ = r"(?P<seq>[1-9][0-9]{0,9})"
_TAG = r"(?P<tag>[A-Za-z0-9_-]{11})"
_REQUEST_RE = re.compile(
    rf"SB2 {_SEQ} " r"(?:(?P<lverb>STRIKE) (?P<level>0|[1-9][0-9]?|100)|(?P<pverb>STORM|FIRE|HEART|PING)) " rf"{_TAG}"
)
_REPLY_RE = re.compile(
    rf"SB2 {_SEQ} "
    r"(?:(?P<ok>OK) (?P<bolts>0|[1-9][0-9]{0,6})|(?P<stale>STALE) (?P<highest>0|[1-9][0-9]{0,9})"
    r"|(?P<status>CLOSED|BUSY|ERR|PONG)) "
    rf"{_TAG}"
)


class LinkKey:
    """The shared link secret. Never logged, never sent."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < SECRET_BYTES:
            raise ValueError(f"link secret must be at least {SECRET_BYTES} bytes")
        self._secret = secret

    @classmethod
    def from_base64(cls, text: str) -> LinkKey:
        try:
            raw = base64.b64decode(text.strip(), validate=True)
        except ValueError as err:
            raise ValueError("link secret is not valid base64") from err
        return cls(raw)

    def tag(self, direction: str, body: str) -> str:
        mac = hmac.new(self._secret, f"{PREFIX}|{direction}|{body}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac[:8]).decode().rstrip("=")

    def check(self, direction: str, body: str, tag: str) -> bool:
        return hmac.compare_digest(self.tag(direction, body), tag)

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return "LinkKey(<redacted>)"


def _valid_seq(seq: int) -> bool:
    return isinstance(seq, int) and 1 <= seq <= SEQ_MAX


@dataclass(frozen=True)
class Request:
    seq: int
    verb: str
    level: int | None = None  # 0..100, STRIKE only

    def body(self) -> str:
        if not _valid_seq(self.seq):
            raise ValueError(f"bad seq {self.seq!r}")
        if self.verb in VERBS_WITH_LEVEL:
            if self.level is None or not 0 <= self.level <= 100:
                raise ValueError(f"{self.verb} needs a level 0..100")
            return f"{self.seq} {self.verb} {self.level}"
        if self.verb in VERBS_PLAIN:
            if self.level is not None:
                raise ValueError(f"{self.verb} takes no level")
            return f"{self.seq} {self.verb}"
        raise ValueError(f"unknown verb {self.verb!r}")

    def encode(self, key: LinkKey) -> str:
        body = self.body()
        return f"{PREFIX} {body} {key.tag('req', body)}"


@dataclass(frozen=True)
class Reply:
    seq: int
    status: str
    value: int | None = None  # OK: bolts tonight; STALE: the translator's highest seq

    @property
    def bolts(self) -> int | None:
        return self.value if self.status == STATUS_OK else None

    def body(self) -> str:
        if not _valid_seq(self.seq):
            raise ValueError(f"bad seq {self.seq!r}")
        if self.status == STATUS_OK:
            if self.value is None or not 0 <= self.value <= 9_999_999:
                raise ValueError("OK needs a bolt count")
            return f"{self.seq} OK {self.value}"
        if self.status == STATUS_STALE:
            if self.value is None or not 0 <= self.value <= SEQ_MAX:
                raise ValueError("STALE needs the highest seq")
            return f"{self.seq} STALE {self.value}"
        if self.status in STATUSES:
            return f"{self.seq} {self.status}"
        raise ValueError(f"unknown status {self.status!r}")

    def encode(self, key: LinkKey) -> str:
        body = self.body()
        return f"{PREFIX} {body} {key.tag('rep', body)}"


def _body_and_tag(text: str) -> tuple[str, str]:
    head, _, tag = text.rpartition(" ")
    return head[len(PREFIX) + 1 :], tag


def parse_request(text: object, key: LinkKey) -> Request | None:
    """Strictly parse and authenticate a request. None for anything else."""
    if not isinstance(text, str) or len(text) > MAX_LEN:
        return None
    m = _REQUEST_RE.fullmatch(text)
    if not m:
        return None
    seq = int(m["seq"])
    if not _valid_seq(seq):
        return None
    body, tag = _body_and_tag(text)
    if not key.check("req", body, tag):
        return None
    if m["lverb"]:
        return Request(seq, m["lverb"], int(m["level"]))
    return Request(seq, m["pverb"])


def parse_reply(text: object, key: LinkKey) -> Reply | None:
    """Strictly parse and authenticate a reply. None for anything else."""
    if not isinstance(text, str) or len(text) > MAX_LEN:
        return None
    m = _REPLY_RE.fullmatch(text)
    if not m:
        return None
    seq = int(m["seq"])
    if not _valid_seq(seq):
        return None
    body, tag = _body_and_tag(text)
    if not key.check("rep", body, tag):
        return None
    if m["ok"]:
        return Reply(seq, STATUS_OK, int(m["bolts"]))
    if m["stale"]:
        highest = int(m["highest"])
        return Reply(seq, STATUS_STALE, highest) if highest <= SEQ_MAX else None
    return Reply(seq, m["status"])


class ReplayWindow:
    """Accept each sequence number at most once, and nothing too old.

    A sliding window (as in IPsec): ``highest`` is the largest seq accepted so far;
    a seq is fresh if it is above ``highest``, or within the last ``size`` numbers and
    not yet seen. That tolerates requests arriving slightly out of order (several guests
    at once) while rejecting every replay. ``highest`` must survive restarts; the caller
    persists it.
    """

    def __init__(self, highest: int = 0, size: int = 64) -> None:
        self.highest = highest
        self.size = size
        self._seen: set[int] = set()

    def is_fresh(self, seq: int) -> bool:
        if seq > self.highest:
            return True
        return seq > self.highest - self.size and seq not in self._seen

    def accept(self, seq: int) -> None:
        self._seen.add(seq)
        if seq > self.highest:
            self.highest = seq
        floor = self.highest - self.size
        self._seen = {s for s in self._seen if s > floor}


# Guest-facing command words (web buttons and mesh DMs) -> what crosses the boundary.
GUEST_ACTIONS: dict[str, tuple[str, int | None]] = {
    "STRIKE": ("STRIKE", 80),
    "BOOM": ("STRIKE", 100),
    "STORM": ("STORM", None),
    "FIRE": ("FIRE", None),
    "HEART": ("HEART", None),
}

# Menu numbers a mesh user can reply with instead of the word.
MENU_NUMBERS = {"1": "STRIKE", "2": "BOOM", "3": "STORM", "4": "FIRE", "5": "HEART", "6": "WHO"}
