"""Read API for the health dashboard.

Tenant scoping (the documented choice): every request carries an
``X-Tenant-Id`` header identifying the caller's tenant.

- Missing header -> **401**.
- Header that does not match the tenant in the path / query / alert ->
  **403**. The same rule applies to ack/resolve: you can only act on your
  own tenant's alerts.
- Unknown alert id -> **404**; illegal lifecycle transition -> **409**.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from . import metrics
from .clock import FixedClock, SystemClock
from .generator import FIXTURE_NOW, build_fixture_events, generate_synthetic_dataset
from .health import compute_health
from .models import IllegalTransition
from .monitors import DEFAULT_MONITORS, evaluate_monitors
from .store import Store


class EvaluateRequest(BaseModel):
    tenant_ids: list[str] | None = None


def require_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="missing X-Tenant-Id header")
    return x_tenant_id


def _check_scope(caller: str, tenant_id: str) -> None:
    if caller != tenant_id:
        raise HTTPException(status_code=403, detail="cross-tenant access denied")


def create_app(store: Store | None = None, clock=None, monitors=None) -> FastAPI:
    store = store if store is not None else Store()
    clock = clock if clock is not None else SystemClock()
    monitors = monitors if monitors is not None else list(DEFAULT_MONITORS)

    app = FastAPI(title="fde-health-dashboard", version="0.1.0")
    app.state.store = store
    app.state.clock = clock
    app.state.monitors = monitors

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "tenants": store.tenants()}

    @app.get("/tenants/{tenant_id}/health")
    def tenant_health(tenant_id: str, caller: str = Depends(require_tenant)):
        _check_scope(caller, tenant_id)
        now = clock.now()
        return compute_health(store.events_for(tenant_id), tenant_id, now)

    @app.get("/tenants/{tenant_id}/adoption")
    def tenant_adoption(
        tenant_id: str,
        days: int = Query(default=7, ge=1, le=90),
        caller: str = Depends(require_tenant),
    ):
        _check_scope(caller, tenant_id)
        now = clock.now()
        start = now - timedelta(days=days)
        events = store.events_for(tenant_id)
        return {
            "tenant_id": tenant_id,
            "as_of": now.isoformat(),
            "window_days": days,
            "active_users": metrics.active_users(events, tenant_id, start, now),
            "adoption": metrics.feature_adoption(events, tenant_id, start, now),
        }

    @app.get("/alerts")
    def list_alerts(tenant: str = Query(...), caller: str = Depends(require_tenant)):
        _check_scope(caller, tenant)
        return [a.to_dict() for a in store.list_alerts(tenant)]

    @app.post("/alerts/evaluate")
    def evaluate(caller: str = Depends(require_tenant)):
        # Evaluation is run by the caller's own tenant scope only.
        touched = evaluate_monitors(store, monitors, clock.now(), tenant_ids=[caller])
        return [a.to_dict() for a in touched]

    def _act(alert_id: int, to_status: str, caller: str):
        alert = store.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        _check_scope(caller, alert.tenant_id)
        try:
            updated = store.transition(alert_id, to_status, clock.now(), "manual")
        except IllegalTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return updated.to_dict()

    @app.post("/alerts/{alert_id}/ack")
    def ack(alert_id: int, caller: str = Depends(require_tenant)):
        return _act(alert_id, "acknowledged", caller)

    @app.post("/alerts/{alert_id}/resolve")
    def resolve(alert_id: int, caller: str = Depends(require_tenant)):
        return _act(alert_id, "resolved", caller)

    return app


def build_demo_app() -> FastAPI:
    """App seeded with the synthetic dataset, clock pinned at FIXTURE_NOW."""
    store = Store()
    store.add_events(generate_synthetic_dataset())
    store.add_events(build_fixture_events())
    return create_app(store=store, clock=FixedClock(FIXTURE_NOW))


app = build_demo_app()
