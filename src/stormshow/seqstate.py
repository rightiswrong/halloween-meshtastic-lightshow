"""Persistent sequence numbers for the signed link (SECURITY-AUDIT.md, F-01).

The edge must never reuse a sequence number, even across reboots, and the translator
must remember the highest one it accepted. Both are kept in small files under the
services' systemd ``StateDirectory``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .protocol import SEQ_MAX, ReplayWindow

log = logging.getLogger(__name__)


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        log.error("could not read %s", path)
        return {}


class SendCounter:
    """The edge's outgoing sequence numbers.

    To spare the SD card, numbers are reserved in blocks: the file holds the end of the
    current block, written *before* any number in it is used. After a crash the counter
    resumes at the next block, so a number is never reused (some are simply skipped).
    """

    def __init__(self, path: str | None, block: int = 64) -> None:
        self._path = Path(path) if path else None
        self._block = block
        self._lock = threading.Lock()
        reserved = int(_read(self._path).get("reserved", 0))
        self._next = reserved + 1
        self._reserved = reserved

    def next(self) -> int:
        with self._lock:
            if self._next > self._reserved:
                self._reserve(self._next + self._block - 1)
            seq = self._next
            self._next += 1
            return seq

    def jump_past(self, highest: int) -> None:
        """Skip ahead after the translator answered STALE (e.g. this box was re-imaged)."""
        with self._lock:
            if highest >= self._next:
                log.warning("translator has seen seq %d; jumping ahead", highest)
                self._next = highest + 1
                self._reserve(self._next + self._block - 1)

    def _reserve(self, end: int) -> None:
        if end > SEQ_MAX:
            raise RuntimeError("sequence numbers exhausted: rotate the link secret")
        self._reserved = end
        if self._path:
            _write_atomic(self._path, {"reserved": end})


class ReceiveWindow:
    """The translator's replay window, with its high-water mark saved to disk."""

    def __init__(self, path: str | None, size: int = 64) -> None:
        self._path = Path(path) if path else None
        self._window = ReplayWindow(int(_read(self._path).get("highest", 0)), size)

    @property
    def highest(self) -> int:
        return self._window.highest

    @property
    def size(self) -> int:
        return self._window.size

    def is_fresh(self, seq: int) -> bool:
        return self._window.is_fresh(seq)

    def accept(self, seq: int) -> None:
        before = self._window.highest
        self._window.accept(seq)
        if self._path and self._window.highest != before:
            _write_atomic(self._path, {"highest": self._window.highest})
