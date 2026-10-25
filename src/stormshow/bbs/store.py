"""A small SQLite log of summons on the edge (who, what, outcome).

Rows older than ``retention_days`` are deleted (SECURITY-AUDIT.md F-17); 0 keeps nothing.
"""

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
    def __init__(self, path: str, retention_days: int = 7) -> None:
        self._retention_s = retention_days * 86400
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._inserts = 0
        self.purge()

    def purge(self) -> int:
        """Delete rows past the retention period. Returns how many."""
        with self._lock:
            cur = self._db.execute("DELETE FROM summons WHERE at < ?", (time.time() - self._retention_s,))
            self._db.commit()
            return cur.rowcount

    def log(self, source: str, name: str, command: str, status: str, bolts: int | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO summons (at, source, name, command, status, bolts) VALUES (?,?,?,?,?,?)",
                (time.time(), source, name[:40], command, status, bolts),
            )
            self._db.commit()
            self._inserts += 1
            due = self._inserts % 50 == 0 or self._retention_s == 0
        if due:
            self.purge()

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
