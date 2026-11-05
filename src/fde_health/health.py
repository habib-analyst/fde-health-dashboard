"""Customer health score: one documented weighted formula, clamped 0-100.

All components are computed over the trailing 7-day window ending at the
injected clock's ``now`` (trend compares that window with the 7 days
before it). Each component is first normalised to 0-100:

- ``adoption_score``  = mean feature adoption % across the features seen
  in the window (already 0-100).
- ``trend_score``     = clamp(50 + trend_delta_pct, 0, 100) — flat usage
  scores 50, +50% growth saturates at 100, -50% scores 0.
- ``error_score``     = clamp(100 - error_rate_pct * 5, 0, 100) — a 20%
  error rate scores 0.
- ``latency_score``   = 100 when p95 <= 200 ms; loses 1 point per 10 ms
  above 200 ms, so a 1200 ms p95 scores 0; clamped 0-100.

Health = 0.35 * adoption + 0.25 * trend + 0.20 * error + 0.20 * latency,
rounded to 2 decimals and clamped to 0-100.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import metrics
from .models import Event

WEIGHTS = {"adoption": 0.35, "trend": 0.25, "error": 0.20, "latency": 0.20}
WINDOW_DAYS = 7


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def trend_score(trend_pct: float) -> float:
    return _clamp(50.0 + trend_pct)


def error_score(error_rate_pct: float) -> float:
    return _clamp(100.0 - error_rate_pct * 5.0)


def latency_score(p95_ms: float) -> float:
    if p95_ms <= 200.0:
        return 100.0
    return _clamp(100.0 - (p95_ms - 200.0) / 10.0)


def compute_health(events: list[Event], tenant_id: str, now: datetime) -> dict:
    start = now - timedelta(days=WINDOW_DAYS)
    adoption = metrics.feature_adoption(events, tenant_id, start, now)
    adoption_value = metrics.mean_adoption(adoption)
    trend_pct = metrics.usage_trend_pct(events, tenant_id, now, WINDOW_DAYS)
    err = metrics.error_rate(events, tenant_id, start, now)
    p95 = metrics.p95_latency(events, tenant_id, start, now)

    components = {
        "adoption_score": adoption_value,
        "trend_score": trend_score(trend_pct),
        "error_score": error_score(err),
        "latency_score": latency_score(p95),
    }
    score = round(
        _clamp(
            WEIGHTS["adoption"] * components["adoption_score"]
            + WEIGHTS["trend"] * components["trend_score"]
            + WEIGHTS["error"] * components["error_score"]
            + WEIGHTS["latency"] * components["latency_score"]
        ),
        2,
    )
    return {
        "tenant_id": tenant_id,
        "as_of": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "score": score,
        "components": components,
        "inputs": {
            "adoption": adoption,
            "mean_adoption_pct": adoption_value,
            "trend_delta_pct": trend_pct,
            "error_rate_pct": err,
            "p95_latency_ms": p95,
            "active_users": metrics.active_users(events, tenant_id, start, now),
            "dau": metrics.active_users_rolling(events, tenant_id, now, 1),
            "wau": metrics.active_users_rolling(events, tenant_id, now, 7),
            "mau": metrics.active_users_rolling(events, tenant_id, now, 30),
        },
    }
