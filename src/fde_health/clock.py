"""Injected clock: every window calculation takes `now` from a clock,
never from the wall, so rolling windows are exact in tests and demos."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """A clock pinned to one instant; `set`/`advance` move it explicitly."""

    def __init__(self, now: datetime):
        if now.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = now

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(**kwargs)
