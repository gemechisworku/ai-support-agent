"""Sliding-window rate limits (GW-R9), keyed by session, session+tool, or credential.

Uses `supportagent.common.clock`, so tests drive time with `freeze_clock`. Limits (config.RateLimits):
per session 20/min and 150 per call, `search_policy` 10/min, per credential 120/min.
"""

import datetime as dt
from collections import deque
from collections.abc import Callable

from supportagent.common import clock

MINUTE = dt.timedelta(minutes=1)


class RateLimiter:
    def __init__(self, now: Callable[[], dt.datetime] = clock.now) -> None:
        self._now = now
        self._hits: dict[str, deque[dt.datetime]] = {}

    def allow(self, key: str, limit: int, window: dt.timedelta = MINUTE) -> bool:
        """Record a hit for `key` and return True, unless `limit` hits already fall inside `window`."""
        now = self._now()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def forget(self, prefix: str) -> None:
        for key in [k for k in self._hits if k.startswith(prefix)]:
            del self._hits[key]
