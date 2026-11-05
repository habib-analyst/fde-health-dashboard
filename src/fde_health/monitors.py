"""SLA monitor evaluation over the injected clock.

Rules (pinned by tests):

- A monitor evaluates its metric over the rolling window
  ``[now - window, now)`` for every tenant that has events.
- **Breach** (metric strictly greater than the threshold):
  - if an alert with the same dedupe key (tenant + monitor + window
    start) already exists, it is refreshed in place — never duplicated;
  - else if a non-resolved alert for the same tenant + monitor exists
    from an earlier window, it is refreshed in place (one active alert
    per tenant + monitor at any time);
  - otherwise a new ``open`` alert is created.
- **No breach**: an ``open`` alert for that tenant + monitor is
  auto-resolved (``resolution = "auto_recovered"``). An ``acknowledged``
  alert is left alone — a human owns it until they resolve it manually.
- Resolved alerts are retained forever as history; a later breach in a
  new window opens a fresh alert.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import metrics
from .models import Alert, MonitorConfig
from .store import Store

DEFAULT_MONITORS = [
    MonitorConfig(name="api-error-rate", metric="error_rate", threshold=5.0, window_minutes=60),
    MonitorConfig(name="api-p95-latency", metric="p95_latency", threshold=500.0, window_minutes=60),
]


def _metric_value(store: Store, config: MonitorConfig, tenant_id: str, start, end) -> float:
    events = store.events_for(tenant_id)
    if config.metric == "error_rate":
        return metrics.error_rate(events, tenant_id, start, end)
    return metrics.p95_latency(events, tenant_id, start, end)


def evaluate_monitors(
    store: Store, configs: list[MonitorConfig], now: datetime, tenant_ids: list[str] | None = None
) -> list[Alert]:
    """Evaluate every monitor for every tenant; return alerts touched/created."""
    tenants = tenant_ids if tenant_ids is not None else store.tenants()
    touched: list[Alert] = []
    for tenant_id in tenants:
        for config in configs:
            start = now - timedelta(minutes=config.window_minutes)
            value = _metric_value(store, config, tenant_id, start, now)
            dedupe_key = f"{tenant_id}|{config.name}|{start.isoformat()}"
            if value > config.threshold:
                existing = store.find_by_dedupe_key(dedupe_key)
                if existing is not None:
                    # One alert per tenant+monitor+window, ever: refresh a
                    # live one, never reopen a resolved one for this window.
                    if existing.status != "resolved":
                        touched.append(store.touch_alert(existing, value, now))
                    continue
                active = store.find_active_alert(tenant_id, config.name)
                if active is not None:
                    touched.append(store.touch_alert(active, value, now))
                    continue
                alert = Alert(
                    id=None,
                    tenant_id=tenant_id,
                    monitor_name=config.name,
                    metric=config.metric,
                    threshold=config.threshold,
                    observed_value=value,
                    window_start=start,
                    window_end=now,
                    dedupe_key=dedupe_key,
                )
                touched.append(store.create_alert(alert, now))
            else:
                active = store.find_active_alert(tenant_id, config.name)
                if active is not None and active.status == "open":
                    touched.append(store.transition(active.id, "resolved", now, "auto_recovered"))
    return touched
