"""A small SQLite log of summons on the edge (who, what, outcome)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS summons (
    id      INTEGER PRIMARY KEY,
    at      REAL NOT NULL,
    source  TEXT NOT NULL,
    name    TEXT NOT NULL,
    command TEXT NOT NULL,
    status  TEXT NOT NULL,
    bolts   INTEGER
);
"""


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def log(self, source: str, name: str, command: str, status: str, bolts: int | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO summons (at, source, name, command, status, bolts) VALUES (?,?,?,?,?,?)",
                (time.time(), source, name[:40], command, status, bolts),
            )
            self._db.commit()

    def last(self) -> tuple[str | None, int | None]:
        """The last successful summoner and bolt count (survives a restart)."""
        with self._lock:
            row = self._db.execute(
                "SELECT name, bolts FROM summons WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def close(self) -> None:
        with self._lock:
            self._db.close()
