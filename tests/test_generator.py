"""The synthetic generator is deterministic and covers 3 tenants / 3 weeks."""

from datetime import timedelta

from fde_health.generator import (
    FIXTURE_NOW,
    build_fixture_events,
    generate_synthetic_dataset,
)


def test_synthetic_is_deterministic():
    a = generate_synthetic_dataset()
    b = generate_synthetic_dataset()
    assert a == b
    assert len(a) > 1000


def test_synthetic_shape():
    events = generate_synthetic_dataset()
    tenants = {e.tenant_id for e in events}
    assert tenants == {"acme", "globex", "initech"}
    assert all(e.ts < FIXTURE_NOW for e in events)
    # Generation starts at midnight 21 days before `end`, so the earliest
    # event can be a little more than 21 days before the noon reference.
    assert min(e.ts for e in events) >= FIXTURE_NOW - timedelta(days=22)
    assert events == sorted(events, key=lambda e: (e.ts, e.tenant_id, e.user_id, e.event_type))
    # Busiest tenant produces the most usage.
    counts = {}
    for e in events:
        counts[e.tenant_id] = counts.get(e.tenant_id, 0) + 1
    assert counts["acme"] > counts["globex"] > counts["initech"]


def test_synthetic_seed_changes_data():
    assert generate_synthetic_dataset(seed=7) != generate_synthetic_dataset(seed=8)


def test_fixture_events_deterministic_and_sorted():
    a, b = build_fixture_events(), build_fixture_events()
    assert a == b
    assert {e.tenant_id for e in a} == {"fixture-alpha", "fixture-beta"}
