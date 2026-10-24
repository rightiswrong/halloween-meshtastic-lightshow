"""Token buckets: one per guest (web client or mesh node) plus a global one."""

from __future__ import annotations

import threading
from collections import OrderedDict


class TokenBucket:
    """Classic token bucket. ``rate`` tokens per second, at most ``burst`` stored."""

    def __init__(self, rate: float, burst: float, now: float) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.stamp = now

    def _refill(self, now: float) -> None:
        if now > self.stamp:
            self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
            self.stamp = now

    def take(self, now: float) -> bool:
        self._refill(now)
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def refund(self) -> None:
        self.tokens = min(self.burst, self.tokens + 1)

    def retry_after(self, now: float) -> float:
        """Seconds until the next token is available (0 if one is available now)."""
        self._refill(now)
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate


class GuestLimiter:
    """A per-key cooldown (one summon per ``cooldown_s``) and a shared global bucket.

    Thread-safe. Keys are pruned least-recently-used beyond ``max_keys`` so a stream of
    new web clients can't grow memory without bound.
    """

    def __init__(
        self,
        cooldown_s: float,
        global_rate_per_min: float,
        global_burst: int,
        now: float,
        max_keys: int = 2048,
    ) -> None:
        self._cooldown = cooldown_s
        self._global = TokenBucket(global_rate_per_min / 60.0, global_burst, now)
        self._keys: OrderedDict[str, TokenBucket] = OrderedDict()
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def _bucket(self, key: str, now: float) -> TokenBucket:
        bucket = self._keys.get(key)
        if bucket is None:
            bucket = TokenBucket(1.0 / self._cooldown, 1, now)
            self._keys[key] = bucket
            while len(self._keys) > self._max_keys:
                self._keys.popitem(last=False)
        else:
            self._keys.move_to_end(key)
        return bucket

    def take(self, key: str, now: float) -> float:
        """Try to spend one summon. Returns 0 on success, else seconds to wait."""
        with self._lock:
            mine = self._bucket(key, now)
            wait = mine.retry_after(now)
            if wait > 0:
                return wait
            if not self._global.take(now):
                return max(1.0, self._global.retry_after(now))
            mine.take(now)
            return 0.0

    def refund(self, key: str) -> None:
        """Give a summon back (the action never happened, e.g. a timeout)."""
        with self._lock:
            bucket = self._keys.get(key)
            if bucket is not None:
                bucket.refund()
            self._global.refund()
