"""SLA monitor evaluation, alert dedupe, lifecycle and history."""

from datetime import timedelta

import pytest

from fde_health.generator import FIXTURE_NOW, generate_synthetic_dataset
from fde_health.models import IllegalTransition, MonitorConfig
from fde_health.monitors import evaluate_monitors
from fde_health.store import Store

ERROR_MONITOR = MonitorConfig("api-error-rate", "error_rate", 5.0, 60)
LATENCY_MONITOR = MonitorConfig("api-p95-latency", "p95_latency", 500.0, 60)


def breach_store(ev, tenant="t1"):
    """8 calls + 2 errors 30 min ago -> error rate 20% > 5% threshold."""
    store = Store()
    ts = FIXTURE_NOW - timedelta(minutes=30)
    events = [ev(tenant=tenant, etype="api_call", ts=ts) for _ in range(8)]
    events += [ev(tenant=tenant, etype="error", ts=ts) for _ in range(2)]
    store.add_events(events)
    return store


def test_breach_opens_exactly_one_alert_and_dedupes(ev):
    store = breach_store(ev)
    touched = evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    assert len(touched) == 1
    alert = touched[0]
    assert alert.status == "open"
    assert alert.tenant_id == "t1"
    assert alert.observed_value == 20.0
    assert alert.dedupe_key == f"t1|api-error-rate|{(FIXTURE_NOW - timedelta(minutes=60)).isoformat()}"
    # Re-evaluation at the same instant: still exactly one alert.
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    assert len(store.list_alerts()) == 1
    # Re-evaluation 10 minutes later (new window, still breaching):
    # the active alert is refreshed, not duplicated.
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW + timedelta(minutes=10))
    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0].status == "open"


def test_no_breach_no_alert(ev):
    store = Store()
    ts = FIXTURE_NOW - timedelta(minutes=30)
    store.add_events([ev(etype="api_call", ts=ts) for _ in range(10)])
    evaluate_monitors(store, [ERROR_MONITOR, LATENCY_MONITOR], FIXTURE_NOW)
    assert store.list_alerts() == []


def test_latency_breach(ev):
    store = Store()
    ts = FIXTURE_NOW - timedelta(minutes=10)
    store.add_events(
        [ev(etype="latency_sample", value=v, ts=ts) for v in (100.0, 200.0, 900.0)]
    )
    touched = evaluate_monitors(store, [LATENCY_MONITOR], FIXTURE_NOW)
    assert len(touched) == 1
    assert touched[0].observed_value == 900.0  # nearest-rank p95 of 3 = max


def test_recovery_auto_resolves_open_alert(ev):
    store = breach_store(ev)
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    # Two hours later the rolling window is clean.
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW + timedelta(hours=2))
    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0].status == "resolved"
    assert alerts[0].resolution == "auto_recovered"
    history = store.alert_history(alerts[0].id)
    assert [h["to_status"] for h in history] == ["open", "resolved"]


def test_acknowledged_alert_is_not_auto_resolved(ev):
    store = breach_store(ev)
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    alert = store.list_alerts()[0]
    store.transition(alert.id, "acknowledged", FIXTURE_NOW, "manual")
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW + timedelta(hours=2))
    assert store.get_alert(alert.id).status == "acknowledged"


def test_resolved_alert_not_reopened_in_same_window(ev):
    store = breach_store(ev)
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    alert = store.list_alerts()[0]
    store.transition(alert.id, "resolved", FIXTURE_NOW, "manual")
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0].status == "resolved"


def test_new_window_breach_after_resolution_opens_new_alert(ev):
    store = breach_store(ev)
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    first = store.list_alerts()[0]
    store.transition(first.id, "resolved", FIXTURE_NOW, "manual")
    # Fresh breach an hour later (new events, new window).
    ts = FIXTURE_NOW + timedelta(minutes=30)
    store.add_events(
        [ev(etype="api_call", ts=ts) for _ in range(8)]
        + [ev(etype="error", ts=ts) for _ in range(2)]
    )
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW + timedelta(hours=1))
    alerts = store.list_alerts()
    assert len(alerts) == 2
    assert alerts[1].status == "open"
    assert alerts[1].dedupe_key != first.dedupe_key


def test_lifecycle_transitions(ev):
    store = breach_store(ev)
    evaluate_monitors(store, [ERROR_MONITOR], FIXTURE_NOW)
    alert = store.list_alerts()[0]

    acked = store.transition(alert.id, "acknowledged", FIXTURE_NOW, "manual")
    assert acked.status == "acknowledged"
    assert acked.acknowledged_at == FIXTURE_NOW
    with pytest.raises(IllegalTransition):
        store.transition(alert.id, "acknowledged", FIXTURE_NOW, "manual")  # ack twice

    resolved = store.transition(alert.id, "resolved", FIXTURE_NOW, "manual")
    assert resolved.status == "resolved"
    assert resolved.resolution == "manual"
    with pytest.raises(IllegalTransition):
        store.transition(alert.id, "resolved", FIXTURE_NOW, "manual")  # resolve twice
    with pytest.raises(IllegalTransition):
        store.transition(alert.id, "acknowledged", FIXTURE_NOW, "manual")  # ack resolved

    history = store.alert_history(alert.id)
    assert [h["to_status"] for h in history] == ["open", "acknowledged", "resolved"]
    assert history[1]["from_status"] == "open"
    assert history[2]["from_status"] == "acknowledged"


def test_synthetic_incident_breaches_only_initech():
    store = Store()
    store.add_events(generate_synthetic_dataset())
    touched = evaluate_monitors(
        store,
        [ERROR_MONITOR, LATENCY_MONITOR],
        FIXTURE_NOW,
    )
    alerts = store.list_alerts()
    assert len(alerts) == 2
    assert {a.tenant_id for a in alerts} == {"initech"}
    assert {a.monitor_name for a in alerts} == {"api-error-rate", "api-p95-latency"}
    by_monitor = {a.monitor_name: a for a in alerts}
    assert by_monitor["api-error-rate"].observed_value == 50.0
    assert by_monitor["api-p95-latency"].observed_value == 950.0
    assert len(touched) == 2
