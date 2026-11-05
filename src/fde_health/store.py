"""SQLite storage for events and alerts.

One store object owns one SQLite connection (``:memory:`` by default, or a
file path). Alert history is retained: transitions append rows to
``alert_history`` and resolved alerts are never deleted.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from .models import LEGAL_TRANSITIONS, Alert, Event, IllegalTransition


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feature TEXT,
                    event_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    ts TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_tenant_ts
                    ON events (tenant_id, ts);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    monitor_name TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    observed_value REAL NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT, updated_at TEXT,
                    acknowledged_at TEXT, resolved_at TEXT,
                    resolution TEXT
                );
                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id INTEGER NOT NULL,
                    from_status TEXT, to_status TEXT NOT NULL,
                    at TEXT NOT NULL, note TEXT
                );
                """
            )

    # ---- events ---------------------------------------------------------
    def add_events(self, events: list[Event]) -> int:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO events (tenant_id, user_id, feature, event_type, value, ts)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (e.tenant_id, e.user_id, e.feature, e.event_type, e.value, _iso(e.ts))
                    for e in events
                ],
            )
        return len(events)

    def events_for(self, tenant_id: str) -> list[Event]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE tenant_id = ? ORDER BY ts", (tenant_id,)
            ).fetchall()
        return [
            Event(
                tenant_id=r["tenant_id"],
                user_id=r["user_id"],
                feature=r["feature"],
                event_type=r["event_type"],
                value=r["value"],
                ts=_parse(r["ts"]),
            )
            for r in rows
        ]

    def tenants(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT tenant_id FROM events ORDER BY tenant_id"
            ).fetchall()
        return [r["tenant_id"] for r in rows]

    # ---- alerts ---------------------------------------------------------
    def _row_to_alert(self, r: sqlite3.Row) -> Alert:
        return Alert(
            id=r["id"],
            tenant_id=r["tenant_id"],
            monitor_name=r["monitor_name"],
            metric=r["metric"],
            threshold=r["threshold"],
            observed_value=r["observed_value"],
            window_start=_parse(r["window_start"]),
            window_end=_parse(r["window_end"]),
            dedupe_key=r["dedupe_key"],
            status=r["status"],
            created_at=_parse(r["created_at"]),
            updated_at=_parse(r["updated_at"]),
            acknowledged_at=_parse(r["acknowledged_at"]),
            resolved_at=_parse(r["resolved_at"]),
            resolution=r["resolution"],
        )

    def create_alert(self, alert: Alert, at: datetime) -> Alert:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO alerts (tenant_id, monitor_name, metric, threshold,"
                " observed_value, window_start, window_end, dedupe_key, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (
                    alert.tenant_id,
                    alert.monitor_name,
                    alert.metric,
                    alert.threshold,
                    alert.observed_value,
                    _iso(alert.window_start),
                    _iso(alert.window_end),
                    alert.dedupe_key,
                    _iso(at),
                    _iso(at),
                ),
            )
            alert.id = cur.lastrowid
            self._conn.execute(
                "INSERT INTO alert_history (alert_id, from_status, to_status, at, note)"
                " VALUES (?, NULL, 'open', ?, 'breach')",
                (alert.id, _iso(at)),
            )
        alert.created_at = alert.updated_at = at
        return alert

    def get_alert(self, alert_id: int) -> Optional[Alert]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
        return self._row_to_alert(row) if row else None

    def find_by_dedupe_key(self, dedupe_key: str) -> Optional[Alert]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM alerts WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
        return self._row_to_alert(row) if row else None

    def find_active_alert(self, tenant_id: str, monitor_name: str) -> Optional[Alert]:
        """The single non-resolved alert for a tenant+monitor, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM alerts WHERE tenant_id = ? AND monitor_name = ?"
                " AND status IN ('open', 'acknowledged') ORDER BY id LIMIT 1",
                (tenant_id, monitor_name),
            ).fetchone()
        return self._row_to_alert(row) if row else None

    def list_alerts(self, tenant_id: Optional[str] = None) -> list[Alert]:
        with self._lock:
            if tenant_id is None:
                rows = self._conn.execute("SELECT * FROM alerts ORDER BY id").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM alerts WHERE tenant_id = ? ORDER BY id", (tenant_id,)
                ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def alert_history(self, alert_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alert_history WHERE alert_id = ? ORDER BY id", (alert_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def touch_alert(self, alert: Alert, observed_value: float, at: datetime) -> Alert:
        """Re-evaluation of a still-breaching alert: refresh, don't duplicate."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE alerts SET observed_value = ?, updated_at = ? WHERE id = ?",
                (observed_value, _iso(at), alert.id),
            )
        alert.observed_value = observed_value
        alert.updated_at = at
        return alert

    def transition(self, alert_id: int, to_status: str, at: datetime, note: str) -> Alert:
        alert = self.get_alert(alert_id)
        if alert is None:
            raise KeyError(f"no alert {alert_id}")
        if to_status not in LEGAL_TRANSITIONS[alert.status]:
            raise IllegalTransition(
                f"cannot move alert {alert_id} from {alert.status} to {to_status}"
            )
        fields = {"status": to_status, "updated_at": _iso(at)}
        if to_status == "acknowledged":
            fields["acknowledged_at"] = _iso(at)
        if to_status == "resolved":
            fields["resolved_at"] = _iso(at)
            fields["resolution"] = "auto_recovered" if note == "auto_recovered" else "manual"
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE alerts SET {assignments} WHERE id = ?",
                (*fields.values(), alert_id),
            )
            self._conn.execute(
                "INSERT INTO alert_history (alert_id, from_status, to_status, at, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (alert_id, alert.status, to_status, _iso(at), note),
            )
        return self.get_alert(alert_id)
