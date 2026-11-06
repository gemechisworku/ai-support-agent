"""A controllable clock (PLAT-R6).

Code that needs "now" or "today" calls `clock.now()` / `clock.today()` instead of `datetime.now()`,
so calling windows, days past due and promise dates are testable::

    from supportagent.common import clock

    with clock.freeze_clock("2026-11-09T14:00:00Z") as fc:
        clock.today()        # date(2026, 11, 9)
        fc.advance(hours=12)

`now()` is always timezone-aware UTC. The freeze is a context variable, so it is visible to asyncio
tasks created inside it and never leaks between threads or tests.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta

_frozen: ContextVar[datetime | None] = ContextVar("frozen_now", default=None)


def now() -> datetime:
    """The current instant, timezone-aware UTC (frozen if inside `freeze_clock`)."""
    frozen = _frozen.get()
    return frozen if frozen is not None else datetime.now(UTC)


def today() -> date:
    """Today's date in UTC. Customer-local dates are computed from `now()` with their timezone."""
    return now().date()


def _parse(at: datetime | str) -> datetime:
    value = datetime.fromisoformat(at) if isinstance(at, str) else at
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("freeze_clock needs a timezone-aware datetime (e.g. '2026-11-09T14:00:00Z')")
    return value.astimezone(UTC)


class FrozenClock:
    """Handle returned by `freeze_clock`; moves the frozen instant."""

    @property
    def now(self) -> datetime:
        return now()

    def set(self, at: datetime | str) -> None:
        _frozen.set(_parse(at))

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> None:
        """Move forward by `delta` or by `timedelta(**kwargs)`, e.g. `advance(days=1)`."""
        _frozen.set(now() + (delta if delta is not None else timedelta(**kwargs)))


@contextmanager
def freeze_clock(at: datetime | str) -> Iterator[FrozenClock]:
    token = _frozen.set(_parse(at))
    try:
        yield FrozenClock()
    finally:
        _frozen.reset(token)
