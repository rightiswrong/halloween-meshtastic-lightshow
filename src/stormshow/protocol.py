"""The private-channel wire protocol between the edge BBS and the translator.

Everything that crosses the trust boundary is one short line of ASCII text on the
private Meshtastic channel. The translator accepts *only* lines that match the
grammar below exactly; anything else is dropped. Keep this file small and strict:
it is the whole attack surface of the trusted side.

Request (edge -> translator), broadcast on the private channel::

    SB1 <rid> STRIKE <0..100>
    SB1 <rid> STORM
    SB1 <rid> FIRE
    SB1 <rid> HEART
    SB1 <rid> PING

Reply (translator -> edge), broadcast on the private channel::

    SB1 <rid> OK <bolts>      the action ran; <bolts> = summons tonight
    SB1 <rid> CLOSED          show hours are off
    SB1 <rid> BUSY            translator rate limit hit
    SB1 <rid> ERR             Home Assistant refused or was unreachable
    SB1 <rid> PONG            reply to PING (no action taken)

``rid`` is four lowercase hex digits chosen by the edge, so replies can be matched
to requests and duplicate deliveries can be recognised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "SB1"
MAX_LEN = 40

VERBS_WITH_LEVEL = frozenset({"STRIKE"})
VERBS_PLAIN = frozenset({"STORM", "FIRE", "HEART", "PING"})
VERBS = VERBS_WITH_LEVEL | VERBS_PLAIN

STATUS_OK = "OK"
STATUS_CLOSED = "CLOSED"
STATUS_BUSY = "BUSY"
STATUS_ERR = "ERR"
STATUS_PONG = "PONG"
STATUSES = frozenset({STATUS_OK, STATUS_CLOSED, STATUS_BUSY, STATUS_ERR, STATUS_PONG})

_REQUEST_RE = re.compile(
    r"SB1 (?P<rid>[0-9a-f]{4}) " r"(?:(?P<lverb>STRIKE) (?P<level>0|[1-9][0-9]?|100)|(?P<pverb>STORM|FIRE|HEART|PING))"
)
_REPLY_RE = re.compile(
    r"SB1 (?P<rid>[0-9a-f]{4}) " r"(?:(?P<ok>OK) (?P<bolts>0|[1-9][0-9]{0,6})|(?P<status>CLOSED|BUSY|ERR|PONG))"
)
_RID_RE = re.compile(r"[0-9a-f]{4}")


@dataclass(frozen=True)
class Request:
    rid: str
    verb: str
    level: int | None = None  # 0..100, STRIKE only

    def encode(self) -> str:
        if not _RID_RE.fullmatch(self.rid):
            raise ValueError(f"bad rid {self.rid!r}")
        if self.verb in VERBS_WITH_LEVEL:
            if self.level is None or not 0 <= self.level <= 100:
                raise ValueError(f"{self.verb} needs a level 0..100")
            return f"{PREFIX} {self.rid} {self.verb} {self.level}"
        if self.verb in VERBS_PLAIN:
            if self.level is not None:
                raise ValueError(f"{self.verb} takes no level")
            return f"{PREFIX} {self.rid} {self.verb}"
        raise ValueError(f"unknown verb {self.verb!r}")


@dataclass(frozen=True)
class Reply:
    rid: str
    status: str
    bolts: int | None = None  # OK only

    def encode(self) -> str:
        if not _RID_RE.fullmatch(self.rid):
            raise ValueError(f"bad rid {self.rid!r}")
        if self.status == STATUS_OK:
            if self.bolts is None or not 0 <= self.bolts <= 9_999_999:
                raise ValueError("OK needs a bolt count")
            return f"{PREFIX} {self.rid} OK {self.bolts}"
        if self.status in STATUSES:
            return f"{PREFIX} {self.rid} {self.status}"
        raise ValueError(f"unknown status {self.status!r}")


def parse_request(text: object) -> Request | None:
    """Strictly parse a request line. Returns None for anything not in the grammar."""
    if not isinstance(text, str) or len(text) > MAX_LEN:
        return None
    m = _REQUEST_RE.fullmatch(text)
    if not m:
        return None
    if m["lverb"]:
        return Request(m["rid"], m["lverb"], int(m["level"]))
    return Request(m["rid"], m["pverb"])


def parse_reply(text: object) -> Reply | None:
    """Strictly parse a reply line. Returns None for anything not in the grammar."""
    if not isinstance(text, str) or len(text) > MAX_LEN:
        return None
    m = _REPLY_RE.fullmatch(text)
    if not m:
        return None
    if m["ok"]:
        return Reply(m["rid"], STATUS_OK, int(m["bolts"]))
    return Reply(m["rid"], m["status"])


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
