"""Core data model: usage events, SLA monitors and alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

EventType = Literal["api_call", "feature_use", "error", "latency_sample"]
AlertStatus = Literal["open", "acknowledged", "resolved"]

#: Event types that count as a user being *active*.
ACTIVITY_TYPES = ("api_call", "feature_use")


@dataclass(frozen=True)
class Event:
    tenant_id: str
    user_id: str
    feature: Optional[str]
    event_type: EventType
    value: float
    ts: datetime

    def __post_init__(self):
        if self.ts.tzinfo is None:
            raise ValueError("event ts must be timezone-aware")
        if self.event_type not in ("api_call", "feature_use", "error", "latency_sample"):
            raise ValueError(f"unknown event_type: {self.event_type!r}")


@dataclass(frozen=True)
class MonitorConfig:
    """One SLA monitor: a metric, a threshold, a rolling window."""

    name: str
    metric: Literal["error_rate", "p95_latency"]
    threshold: float  # percent for error_rate, milliseconds for p95_latency
    window_minutes: int = 60

    def __post_init__(self):
        if self.window_minutes <= 0:
            raise ValueError("window_minutes must be positive")


@dataclass
class Alert:
    id: Optional[int]
    tenant_id: str
    monitor_name: str
    metric: str
    threshold: float
    observed_value: float
    window_start: datetime
    window_end: datetime
    dedupe_key: str
    status: AlertStatus = "open"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None  # "manual" | "auto_recovered"
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        def iso(dt):
            return dt.isoformat() if dt is not None else None

        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "monitor_name": self.monitor_name,
            "metric": self.metric,
            "threshold": self.threshold,
            "observed_value": self.observed_value,
            "window_start": iso(self.window_start),
            "window_end": iso(self.window_end),
            "dedupe_key": self.dedupe_key,
            "status": self.status,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "acknowledged_at": iso(self.acknowledged_at),
            "resolved_at": iso(self.resolved_at),
            "resolution": self.resolution,
        }


class IllegalTransition(Exception):
    """Raised when an alert lifecycle transition is not allowed."""


#: Legal lifecycle transitions. Everything else is rejected.
LEGAL_TRANSITIONS = {
    "open": {"acknowledged", "resolved"},
    "acknowledged": {"resolved"},
    "resolved": set(),
}
