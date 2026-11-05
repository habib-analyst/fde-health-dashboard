"""Metrics engine: pure functions over event lists.

Window convention everywhere: half-open ``[start, end)`` — an event exactly
at ``start`` is included, an event exactly at ``end`` is excluded.

Definitions (pinned by tests):
- *Active user*: a distinct ``user_id`` with at least one ``api_call`` or
  ``feature_use`` event in the window. ``error`` and ``latency_sample``
  events are system signals and never make a user active.
- *Feature adoption*: for each feature, distinct users with a
  ``feature_use`` event for that feature / active users * 100.
- *Error rate*: ``error`` events / (``api_call`` + ``error`` events) * 100.
- *p95 latency*: nearest-rank over ``latency_sample`` values — sort
  ascending, take the element at rank ``ceil(0.95 * n)`` (1-based).
- *Usage trend*: usage events (``api_call`` + ``feature_use``) in the
  current window vs the immediately preceding window of the same length:
  ``(current - previous) / previous * 100``; 0.0 when both are 0, +100.0
  when previous is 0 and current is not.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .models import ACTIVITY_TYPES, Event


def in_window(events: list[Event], tenant_id: str, start: datetime, end: datetime) -> list[Event]:
    return [e for e in events if e.tenant_id == tenant_id and start <= e.ts < end]


def active_users(events: list[Event], tenant_id: str, start: datetime, end: datetime) -> int:
    return len(
        {
            e.user_id
            for e in in_window(events, tenant_id, start, end)
            if e.event_type in ACTIVITY_TYPES
        }
    )


def active_users_rolling(events: list[Event], tenant_id: str, now: datetime, days: int) -> int:
    """DAU (days=1), WAU (days=7) and MAU (days=30) as rolling windows."""
    return active_users(events, tenant_id, now - timedelta(days=days), now)


def usage_count(events: list[Event], tenant_id: str, start: datetime, end: datetime) -> int:
    return sum(
        1
        for e in in_window(events, tenant_id, start, end)
        if e.event_type in ACTIVITY_TYPES
    )


def feature_adoption(
    events: list[Event], tenant_id: str, start: datetime, end: datetime
) -> dict[str, float]:
    """Adoption % per feature seen in the window; {} when nobody is active."""
    actives = active_users(events, tenant_id, start, end)
    if actives == 0:
        return {}
    users_by_feature: dict[str, set] = {}
    for e in in_window(events, tenant_id, start, end):
        if e.event_type == "feature_use" and e.feature:
            users_by_feature.setdefault(e.feature, set()).add(e.user_id)
    return {
        feature: round(len(users) / actives * 100, 4)
        for feature, users in sorted(users_by_feature.items())
    }


def mean_adoption(adoption: dict[str, float]) -> float:
    if not adoption:
        return 0.0
    return sum(adoption.values()) / len(adoption)


def error_rate(events: list[Event], tenant_id: str, start: datetime, end: datetime) -> float:
    windowed = in_window(events, tenant_id, start, end)
    errors = sum(1 for e in windowed if e.event_type == "error")
    calls = sum(1 for e in windowed if e.event_type == "api_call")
    denom = calls + errors
    if denom == 0:
        return 0.0
    return errors / denom * 100


def p95_latency(events: list[Event], tenant_id: str, start: datetime, end: datetime) -> float:
    """Nearest-rank p95 of latency samples; 0.0 when there are no samples."""
    values = sorted(
        e.value
        for e in in_window(events, tenant_id, start, end)
        if e.event_type == "latency_sample"
    )
    if not values:
        return 0.0
    rank = math.ceil(0.95 * len(values))  # 1-based
    return values[rank - 1]


def usage_trend_pct(
    events: list[Event], tenant_id: str, now: datetime, window_days: int = 7
) -> float:
    """Period-over-period usage delta: current window vs the previous one."""
    current = usage_count(
        events, tenant_id, now - timedelta(days=window_days), now
    )
    previous = usage_count(
        events,
        tenant_id,
        now - timedelta(days=2 * window_days),
        now - timedelta(days=window_days),
    )
    if previous == 0:
        return 0.0 if current == 0 else 100.0
    return (current - previous) / previous * 100
