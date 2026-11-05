"""API behaviour: health/adoption reads, alert actions, tenant scoping.

Scoping rule under test (documented in app.py): missing X-Tenant-Id -> 401,
header not matching the tenant being read/acted on -> 403, unknown alert
-> 404, illegal lifecycle transition -> 409.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fde_health.app import create_app
from fde_health.clock import FixedClock
from fde_health.generator import FIXTURE_NOW, build_fixture_events
from fde_health.store import Store

ALPHA = {"X-Tenant-Id": "fixture-alpha"}
BETA = {"X-Tenant-Id": "fixture-beta"}


@pytest.fixture
def client():
    store = Store()
    store.add_events(build_fixture_events())
    app = create_app(store=store, clock=FixedClock(FIXTURE_NOW))
    return TestClient(app)


def test_health_endpoint_pinned(client):
    r = client.get("/tenants/fixture-alpha/health", headers=ALPHA)
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 57.38
    assert body["tenant_id"] == "fixture-alpha"
    assert body["inputs"]["dau"] == 2
    assert body["inputs"]["mau"] == 5


def test_adoption_endpoint(client):
    r = client.get("/tenants/fixture-alpha/adoption", headers=ALPHA)
    assert r.status_code == 200
    body = r.json()
    assert body["active_users"] == 4
    assert body["adoption"] == {
        "dashboard": 100.0,
        "export": 25.0,
        "reports": 50.0,
        "search": 75.0,
    }


def test_cross_tenant_reads_rejected(client):
    assert client.get("/tenants/fixture-alpha/health", headers=BETA).status_code == 403
    assert client.get("/tenants/fixture-alpha/adoption", headers=BETA).status_code == 403
    assert client.get("/alerts", params={"tenant": "fixture-alpha"}, headers=BETA).status_code == 403
    # Missing header -> 401.
    assert client.get("/tenants/fixture-alpha/health").status_code == 401
    assert client.get("/alerts", params={"tenant": "fixture-alpha"}).status_code == 401
    # Own-tenant reads still work.
    assert client.get("/tenants/fixture-beta/health", headers=BETA).status_code == 200


@pytest.fixture
def alert_client(ev):
    """A tenant 'api-a' breaching the error-rate monitor in the last hour."""
    store = Store()
    ts = FIXTURE_NOW - timedelta(minutes=30)
    store.add_events(
        [ev(tenant="api-a", etype="api_call", ts=ts) for _ in range(8)]
        + [ev(tenant="api-a", etype="error", ts=ts) for _ in range(2)]
        + [ev(tenant="api-b", etype="api_call", ts=ts) for _ in range(3)]
    )
    app = create_app(store=store, clock=FixedClock(FIXTURE_NOW))
    return TestClient(app)


def test_alert_flow_via_api(alert_client):
    a = {"X-Tenant-Id": "api-a"}
    b = {"X-Tenant-Id": "api-b"}
    # Evaluate (scoped to the caller's tenant) opens exactly one alert.
    r = alert_client.post("/alerts/evaluate", headers=a)
    assert r.status_code == 200
    assert len(r.json()) == 1
    alert = r.json()[0]
    assert alert["status"] == "open"
    assert alert["observed_value"] == 20.0
    alert_id = alert["id"]

    # Listing is tenant-scoped.
    r = alert_client.get("/alerts", params={"tenant": "api-a"}, headers=a)
    assert [x["id"] for x in r.json()] == [alert_id]
    assert alert_client.get("/alerts", params={"tenant": "api-b"}, headers=b).json() == []

    # Another tenant cannot act on the alert.
    assert alert_client.post(f"/alerts/{alert_id}/ack", headers=b).status_code == 403
    assert alert_client.post(f"/alerts/{alert_id}/resolve", headers=b).status_code == 403

    # Lifecycle: ack -> ack again (409) -> resolve -> resolve again (409).
    r = alert_client.post(f"/alerts/{alert_id}/ack", headers=a)
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    assert alert_client.post(f"/alerts/{alert_id}/ack", headers=a).status_code == 409
    r = alert_client.post(f"/alerts/{alert_id}/resolve", headers=a)
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert alert_client.post(f"/alerts/{alert_id}/resolve", headers=a).status_code == 409

    # Unknown alert -> 404.
    assert alert_client.post("/alerts/9999/ack", headers=a).status_code == 404


def test_evaluate_is_tenant_scoped(alert_client):
    # api-b has no breach; evaluating as api-b must not create api-a alerts.
    b = {"X-Tenant-Id": "api-b"}
    r = alert_client.post("/alerts/evaluate", headers=b)
    assert r.json() == []
    a = {"X-Tenant-Id": "api-a"}
    assert alert_client.get("/alerts", params={"tenant": "api-a"}, headers=a).json() == []
