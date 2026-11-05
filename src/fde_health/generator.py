"""Deterministic synthetic data: a multi-week dataset plus a small
hand-computed fixture dataset.

No wall clock, no unseeded randomness: the same inputs always produce the
same events, byte for byte.

Fixture dataset (``build_fixture_events``), pinned at
``FIXTURE_NOW = 2026-01-15T12:00:00Z``. The aggregates below were worked
out by hand from the construction and are asserted exactly in the tests:

Tenant ``fixture-alpha``
- trailing 7 days: 4 active users; usage = 120 events
  (80 feature_use + 40 api_call); prior 7 days usage = 100 -> trend +20.0%
- adoption: search 75.0, reports 50.0, export 25.0, dashboard 100.0
  -> mean adoption 62.5
- error rate: 10 errors / (40 api_call + 10 errors) = 20.0%
- latency: 20 samples (18 x 150 ms, 300 ms, 400 ms) -> nearest-rank p95
  is the 19th of 20 = 300 ms
- DAU 2, WAU 4, MAU 5 (a fifth user appears once, 2025-12-20)
- health score 57.38 (see health.py for the formula)

Tenant ``fixture-beta``
- trailing 7 days: 2 active users; usage = 100; prior 7 days = 200
  -> trend -50.0%
- adoption: search 100.0, reports 50.0 -> mean 75.0
- error rate 0.0%; latency: 10 samples all 150 ms -> p95 150 ms
- health score 66.25
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .models import Event

FIXTURE_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

FEATURES = ["search", "dashboard", "reports", "export", "alerts"]


def _repeat(events, tenant, user, feature, etype, value, ts, count):
    for _ in range(count):
        events.append(Event(tenant, user, feature, etype, value, ts))


def _at(day: int, hour: int = 9, month: int = 1, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


def build_fixture_events() -> list[Event]:
    events: list[Event] = []
    jan10, jan14, jan15 = _at(10), _at(14, 13), _at(15)
    jan5, dec20 = _at(5), _at(20, month=12, year=2025)
    a = "fixture-alpha"

    # --- alpha, current 7d: feature_use (80 total) ---
    _repeat(events, a, "u1", "search", "feature_use", 1.0, jan15, 6)
    _repeat(events, a, "u1", "search", "feature_use", 1.0, jan10, 4)
    _repeat(events, a, "u2", "search", "feature_use", 1.0, jan15, 6)
    _repeat(events, a, "u2", "search", "feature_use", 1.0, jan10, 4)
    _repeat(events, a, "u3", "search", "feature_use", 1.0, jan10, 10)
    _repeat(events, a, "u1", "reports", "feature_use", 1.0, jan10, 10)
    _repeat(events, a, "u2", "reports", "feature_use", 1.0, jan10, 10)
    _repeat(events, a, "u1", "export", "feature_use", 1.0, jan10, 10)
    for u in ("u1", "u2", "u3", "u4"):
        _repeat(events, a, u, "dashboard", "feature_use", 1.0, jan10, 5)
    # --- alpha, current 7d: api_call (40), errors (10), latency (20) ---
    for u in ("u1", "u2", "u3", "u4"):
        _repeat(events, a, u, None, "api_call", 1.0, jan10, 10)
    _repeat(events, a, "u1", None, "error", 1.0, jan10, 10)
    for v in [150.0] * 18 + [300.0, 400.0]:
        events.append(Event(a, "u1", None, "latency_sample", v, jan10))
    # --- alpha, prior 7d: usage 100 (60 api_call + 40 search) ---
    for u in ("u1", "u2", "u3", "u4"):
        _repeat(events, a, u, None, "api_call", 1.0, jan5, 15)
    _repeat(events, a, "u1", "search", "feature_use", 1.0, jan5, 20)
    _repeat(events, a, "u2", "search", "feature_use", 1.0, jan5, 20)
    # --- alpha, 30d-only user (MAU) ---
    _repeat(events, a, "u5", "search", "feature_use", 1.0, dec20, 1)

    b = "fixture-beta"
    # --- beta, current 7d: usage 100 (50 feature_use + 50 api_call) ---
    _repeat(events, b, "v1", "search", "feature_use", 1.0, jan10, 20)
    _repeat(events, b, "v2", "search", "feature_use", 1.0, jan10, 20)
    _repeat(events, b, "v1", "reports", "feature_use", 1.0, jan10, 10)
    _repeat(events, b, "v1", None, "api_call", 1.0, jan10, 25)
    _repeat(events, b, "v2", None, "api_call", 1.0, jan10, 25)
    for _ in range(10):
        events.append(Event(b, "v1", None, "latency_sample", 150.0, jan10))
    # --- beta, prior 7d: usage 200 ---
    _repeat(events, b, "v1", None, "api_call", 1.0, jan5, 100)
    _repeat(events, b, "v2", None, "api_call", 1.0, jan5, 100)

    return sorted(events, key=lambda e: (e.ts, e.tenant_id, e.user_id, e.event_type))


# ---------------------------------------------------------------------------
# Multi-week synthetic dataset (seeded, deterministic)
# ---------------------------------------------------------------------------

SYNTHETIC_TENANTS = {
    "acme": 18,
    "globex": 10,
    "initech": 6,
}


def generate_synthetic_dataset(
    seed: int = 7, days: int = 21, end: datetime = FIXTURE_NOW
) -> list[Event]:
    """Three tenants, `days` days of usage ending at `end` (exclusive).

    Busier tenants get more users and a higher daily activity rate, one
    tenant (initech) is given a bad final hour — a burst of errors and slow
    samples — so the default SLA monitors breach for exactly that tenant.
    """
    rng = random.Random(seed)
    events: list[Event] = []
    start_day = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    for tenant, n_users in SYNTHETIC_TENANTS.items():
        activity = {"acme": 0.85, "globex": 0.6, "initech": 0.45}[tenant]
        for day in range(days):
            date = start_day + timedelta(days=day)
            for i in range(n_users):
                if rng.random() > activity:
                    continue
                user = f"{tenant}-user-{i + 1:02d}"
                for _ in range(rng.randint(2, 6)):
                    ts = date + timedelta(
                        hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
                    )
                    if ts >= end:
                        continue
                    events.append(Event(tenant, user, None, "api_call", 1.0, ts))
                    feature = rng.choice(FEATURES)
                    events.append(Event(tenant, user, feature, "feature_use", 1.0, ts))
                    if rng.random() < 0.02:
                        events.append(Event(tenant, user, None, "error", 1.0, ts))
                    events.append(
                        Event(tenant, user, None, "latency_sample", float(rng.randint(60, 350)), ts)
                    )
    # initech incident in the final hour before `end`
    incident_start = end - timedelta(minutes=55)
    for i in range(9):
        ts = incident_start + timedelta(minutes=i * 5)
        events.append(Event("initech", "initech-user-01", None, "api_call", 1.0, ts))
        events.append(Event("initech", "initech-user-01", None, "error", 1.0, ts))
        events.append(Event("initech", "initech-user-01", None, "latency_sample", 950.0, ts))
    return sorted(events, key=lambda e: (e.ts, e.tenant_id, e.user_id, e.event_type))
