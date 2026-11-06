"""T-PLAT-05 · PLAT-R6: a controllable clock (`supportagent.common.clock`)."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from supportagent.common import clock
from supportagent.common.clock import FrozenClock, freeze_clock


def test_real_clock_is_utc_and_current() -> None:
    before = datetime.now(UTC)
    now = clock.now()
    assert now.tzinfo is UTC
    assert before <= now <= datetime.now(UTC)
    assert clock.today() == clock.now().date()


def test_freeze_as_context_manager_and_restore() -> None:
    with freeze_clock("2026-11-09T14:00:00Z") as fc:
        assert clock.now() == datetime(2026, 11, 9, 14, tzinfo=UTC)
        assert clock.today() == date(2026, 11, 9)
        assert fc.now == clock.now()
    assert clock.now() != datetime(2026, 11, 9, 14, tzinfo=UTC)


def test_advance_and_set() -> None:
    with freeze_clock(datetime(2026, 11, 9, 23, 30, tzinfo=UTC)) as fc:
        fc.advance(hours=1)
        assert clock.today() == date(2026, 11, 10)
        fc.advance(timedelta(days=2))
        assert clock.now() == datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
        fc.set("2026-12-01T09:00:00+03:00")
        assert clock.now() == datetime(2026, 12, 1, 6, tzinfo=UTC)
        assert clock.now().tzinfo is UTC


def test_nested_freezes_restore_outer() -> None:
    with freeze_clock("2026-11-09T00:00:00Z"):
        with freeze_clock("2030-01-01T00:00:00Z"):
            assert clock.today() == date(2030, 1, 1)
        assert clock.today() == date(2026, 11, 9)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"), freeze_clock("2026-11-09T14:00:00"):
        pass


def test_frozen_clock_is_visible_in_asyncio_tasks() -> None:
    async def read() -> date:
        return clock.today()

    async def main() -> date:
        return await asyncio.create_task(read())

    with freeze_clock("2026-11-09T14:00:00Z"):
        assert asyncio.run(main()) == date(2026, 11, 9)


def test_frozen_clock_fixture(frozen_clock: FrozenClock) -> None:
    assert clock.now() == datetime(2026, 11, 9, 14, tzinfo=UTC)  # the fixture's default instant
    frozen_clock.advance(days=1)
    assert clock.today() == date(2026, 11, 10)
