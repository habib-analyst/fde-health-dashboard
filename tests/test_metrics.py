"""Metric correctness: literal hand-built events + the pinned fixtures."""

from datetime import datetime, timedelta, timezone

from fde_health import metrics
from fde_health.generator import FIXTURE_NOW, build_fixture_events
from fde_health.models import Event

UTC = timezone.utc
START = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)
END = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def test_active_users_counts_distinct_activity_users_only(ev):
    events = [
        ev(user="u1", etype="api_call", ts=START),
        ev(user="u1", etype="feature_use", feature="search", ts=START),
        ev(user="u2", etype="feature_use", feature="search", ts=START),
        ev(user="u3", etype="error", ts=START),  # errors do not activate
        ev(user="u4", etype="latency_sample", value=120.0, ts=START),  # nor latency
        ev(user="u5", etype="api_call", ts=START, tenant="other"),  # other tenant
    ]
    assert metrics.active_users(events, "t1", START, END) == 2


def test_window_is_half_open(ev):
    events = [
        ev(user="before", ts=START - timedelta(seconds=1)),
        ev(user="at_start", ts=START),  # included
        ev(user="inside", ts=END - timedelta(seconds=1)),
        ev(user="at_end", ts=END),  # excluded
    ]
    assert metrics.active_users(events, "t1", START, END) == 2
    # Same boundary rule for latency and errors.
    samples = [
        ev(etype="latency_sample", value=100.0, ts=START),
        ev(etype="latency_sample", value=999.0, ts=END),  # excluded
    ]
    assert metrics.p95_latency(samples, "t1", START, END) == 100.0
    errs = [
        ev(etype="api_call", ts=START),
        ev(etype="error", ts=END),  # excluded -> error rate stays 0
    ]
    assert metrics.error_rate(errs, "t1", START, END) == 0.0


def test_p95_nearest_rank(ev):
    def p95(values):
        events = [ev(etype="latency_sample", value=v, ts=START) for v in values]
        return metrics.p95_latency(events, "t1", START, END)

    assert p95([]) == 0.0
    assert p95([42.0]) == 42.0
    assert p95([10.0, 20.0, 30.0, 40.0]) == 40.0  # rank ceil(.95*4)=4
    assert p95([10.0, 20.0, 30.0]) == 30.0  # rank ceil(.95*3)=3
    # 20 values 1..20 -> rank 19
    assert p95([float(i) for i in range(1, 21)]) == 19.0
    # Unsorted input is sorted first.
    assert p95([40.0, 10.0, 30.0, 20.0]) == 40.0


def test_error_rate(ev):
    events = [ev(etype="api_call", ts=START) for _ in range(8)] + [
        ev(etype="error", ts=START) for _ in range(2)
    ]
    assert metrics.error_rate(events, "t1", START, END) == 20.0
    assert metrics.error_rate([], "t1", START, END) == 0.0


def test_feature_adoption_literal(ev):
    # 4 active users; search used by 3, reports by 1.
    events = []
    for user in ("u1", "u2", "u3", "u4"):
        events.append(ev(user=user, etype="api_call", ts=START))
    for user in ("u1", "u2", "u3"):
        events.append(ev(user=user, etype="feature_use", feature="search", ts=START))
    events.append(ev(user="u1", etype="feature_use", feature="reports", ts=START))
    adoption = metrics.feature_adoption(events, "t1", START, END)
    assert adoption == {"reports": 25.0, "search": 75.0}
    assert metrics.feature_adoption([], "t1", START, END) == {}


def test_usage_trend_literal(ev):
    now = END
    events = []
    # current 7d: 30 usage events; previous 7d: 20 -> +50%
    for _ in range(30):
        events.append(ev(ts=now - timedelta(days=1)))
    for _ in range(20):
        events.append(ev(ts=now - timedelta(days=8)))
    # older than both windows: ignored
    for _ in range(99):
        events.append(ev(ts=now - timedelta(days=20)))
    assert metrics.usage_trend_pct(events, "t1", now, 7) == 50.0
    assert metrics.usage_trend_pct([], "t1", now, 7) == 0.0


# --- pinned fixture tenants (hand-computed in generator.py docstring) ------

FIXTURES = build_fixture_events()


def test_fixture_alpha_actives():
    assert metrics.active_users_rolling(FIXTURES, "fixture-alpha", FIXTURE_NOW, 1) == 2
    assert metrics.active_users_rolling(FIXTURES, "fixture-alpha", FIXTURE_NOW, 7) == 4
    assert metrics.active_users_rolling(FIXTURES, "fixture-alpha", FIXTURE_NOW, 30) == 5


def test_fixture_alpha_metrics():
    start = FIXTURE_NOW - timedelta(days=7)
    adoption = metrics.feature_adoption(FIXTURES, "fixture-alpha", start, FIXTURE_NOW)
    assert adoption == {
        "dashboard": 100.0,
        "export": 25.0,
        "reports": 50.0,
        "search": 75.0,
    }
    assert metrics.mean_adoption(adoption) == 62.5
    assert metrics.error_rate(FIXTURES, "fixture-alpha", start, FIXTURE_NOW) == 20.0
    assert metrics.p95_latency(FIXTURES, "fixture-alpha", start, FIXTURE_NOW) == 300.0
    assert metrics.usage_count(FIXTURES, "fixture-alpha", start, FIXTURE_NOW) == 120
    assert metrics.usage_trend_pct(FIXTURES, "fixture-alpha", FIXTURE_NOW, 7) == 20.0


def test_fixture_beta_metrics():
    start = FIXTURE_NOW - timedelta(days=7)
    adoption = metrics.feature_adoption(FIXTURES, "fixture-beta", start, FIXTURE_NOW)
    assert adoption == {"reports": 50.0, "search": 100.0}
    assert metrics.mean_adoption(adoption) == 75.0
    assert metrics.error_rate(FIXTURES, "fixture-beta", start, FIXTURE_NOW) == 0.0
    assert metrics.p95_latency(FIXTURES, "fixture-beta", start, FIXTURE_NOW) == 150.0
    assert metrics.usage_trend_pct(FIXTURES, "fixture-beta", FIXTURE_NOW, 7) == -50.0
    assert metrics.active_users_rolling(FIXTURES, "fixture-beta", FIXTURE_NOW, 1) == 0
    assert metrics.active_users_rolling(FIXTURES, "fixture-beta", FIXTURE_NOW, 7) == 2
