# fde-health-dashboard

A customer health dashboard backend for a multi-tenant SaaS: the service a
Forward Deployed Engineer puts in front of a customer-success team so they
can see, per tenant, who is actually using the product, which features are
landing, whether usage is growing or shrinking, and whether the service is
keeping its SLA — with alerts when it is not. It ingests per-tenant usage
events, computes adoption and health metrics, evaluates SLA monitors over
rolling windows, and raises deduplicated breach alerts with a small
lifecycle. A tenant-scoped JSON API serves the aggregates. All data is
synthetic and generated deterministically in code; time comes from an
injected clock, so every window is exact and reproducible. Local only, no
network calls, no cost.

## Event model

Every event is one row in SQLite:

| field | meaning |
|---|---|
| `tenant_id` | the customer tenant |
| `user_id` | the end user inside the tenant |
| `feature` | feature name for `feature_use` events, else null |
| `event_type` | `api_call` / `feature_use` / `error` / `latency_sample` |
| `value` | latency in ms for `latency_sample`, otherwise 1.0 |
| `ts` | timezone-aware timestamp |

## Metrics engine (`metrics.py`)

All windows are half-open `[start, end)`: an event exactly at the start is
included, one exactly at the end is excluded. Definitions are pinned by
tests:

- **Active users** — distinct users with an `api_call` or `feature_use`
  event in the window (`error` and `latency_sample` are system signals and
  never activate a user). DAU / WAU / MAU are rolling 1 / 7 / 30-day
  windows ending at the injected clock's `now`.
- **Feature adoption** — for each feature, distinct users with a
  `feature_use` event for it ÷ active users × 100.
- **Usage trend** — usage events (`api_call` + `feature_use`) in the
  current window vs the immediately preceding window of the same length:
  `(current − previous) / previous × 100`; 0 when both are empty.
- **Error rate** — `error` events ÷ (`api_call` + `error` events) × 100.
- **p95 latency** — nearest-rank over `latency_sample` values: sort
  ascending, take the element at rank `ceil(0.95 × n)`.

## SLA monitors and alerts (`monitors.py`)

A monitor is a metric, a threshold and a rolling window. The defaults are
`error_rate > 5%` over 60 minutes and `p95 latency > 500 ms` over 60
minutes; both are plain configuration and can be replaced per deployment.
Evaluation runs against the injected clock:

- A breach opens **exactly one alert per tenant + monitor + window**
  (dedupe key: `tenant|monitor|window_start`). Re-evaluating the same
  window, or a later window while an alert is still active, refreshes the
  alert's observed value in place instead of duplicating it.
- When the metric recovers, an `open` alert auto-resolves
  (`resolution = "auto_recovered"`). An `acknowledged` alert is left alone:
  a human owns it until they resolve it manually.
- A resolved alert is never reopened for the same window; a breach in a
  later window opens a fresh alert. Resolved alerts and the full
  transition history are retained in SQLite (`alert_history`).

Lifecycle: `open → acknowledged → resolved`, plus `open → resolved`.
Every other transition (ack twice, resolve twice, ack a resolved alert)
is rejected — `IllegalTransition` in the store, HTTP 409 in the API.

## Health score (`health.py`)

One weighted formula over the trailing 7 days (trend compares those 7 days
with the 7 before), each component normalised to 0–100 first:

| component | normalisation | weight |
|---|---|---|
| adoption | mean feature adoption % | 0.35 |
| trend | clamp(50 + trend Δ%, 0, 100) | 0.25 |
| error | clamp(100 − error rate % × 5, 0, 100) | 0.20 |
| latency | 100 at p95 ≤ 200 ms, −1 point per 10 ms above, floor 0 | 0.20 |

`health = 0.35·adoption + 0.25·trend + 0.20·error + 0.20·latency`,
rounded to 2 decimals and clamped to 0–100.

## API (`app.py`)

Every request carries an `X-Tenant-Id` header identifying the caller's
tenant. Missing header → **401**; a header that does not match the tenant
in the path, query or alert → **403**; unknown alert → **404**; illegal
lifecycle transition → **409**. That is the scoping rule for every route,
including alert actions — you can only read or act on your own tenant.

| route | returns |
|---|---|
| `GET /tenants/{id}/health` | health score, component scores, and the raw inputs (adoption per feature, trend Δ, error rate, p95, DAU/WAU/MAU) |
| `GET /tenants/{id}/adoption` | per-feature adoption % and active users (`?days=` window, default 7) |
| `GET /alerts?tenant={id}` | the tenant's alerts, all statuses, newest history retained |
| `POST /alerts/evaluate` | evaluates the monitors for the caller's tenant at the clock's `now`; returns touched/created alerts |
| `POST /alerts/{id}/ack` | `open → acknowledged` |
| `POST /alerts/{id}/resolve` | `open`/`acknowledged → resolved` |
| `GET /healthz` | service status and known tenants |

## Data (`generator.py`)

Two deterministic datasets, no wall clock and no unseeded randomness:

- **Synthetic** — 3 tenants (`acme` 18 users, `globex` 10, `initech` 6)
  over 21 days, 5,989 events with the shipped seed. `initech` has a
  scripted incident in the final hour (error burst + 950 ms samples), so
  evaluating the default monitors at the reference instant opens exactly
  two alerts, both for `initech`: error rate 50.0% and p95 950 ms.
- **Fixtures** — two small tenants whose aggregates were worked out by
  hand from the construction, so the tests pin real numbers rather than
  the implementation's own output. At the reference instant
  `2026-01-15T12:00:00Z`:

| | fixture-alpha | fixture-beta |
|---|---|---|
| DAU / WAU / MAU | 2 / 4 / 5 | 0 / 2 / 2 |
| adoption | search 75, reports 50, export 25, dashboard 100 (mean 62.5) | search 100, reports 50 (mean 75.0) |
| usage trend (7d vs prior 7d) | +20.0% (120 vs 100) | −50.0% (100 vs 200) |
| error rate | 20.0% | 0.0% |
| p95 latency | 300 ms | 150 ms |
| **health score** | **57.38** | **66.25** |

## Run it

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -q        # 31 tests
python3 -m fde_health.serve        # demo API on http://127.0.0.1:8000
```

The demo server seeds the synthetic dataset, pins the clock at the
reference instant and evaluates the default monitors once, so
`GET /alerts?tenant=initech` (with header `X-Tenant-Id: initech`) already
shows the incident. In the same seeded app, tenant `acme`'s health score
is 78.48 (mean adoption 96.67, trend −13.19%, error rate 1.90%, p95
333 ms, 18 weekly active users).

## Tests

31 tests, all passing (`python3 -m pytest tests/ -q`):

- `test_metrics.py` (9) — actives, adoption, error rate, nearest-rank p95,
  trend, and half-open window boundaries (events exactly at start/end),
  plus the hand-computed fixture numbers above.
- `test_health.py` (4) — both fixture health scores pinned exactly
  (57.38 and 66.25), component formula edges and clamping.
- `test_monitors.py` (9) — breach opens one alert, dedupe on
  re-evaluation (same window and later window), latency breach, recovery
  auto-resolves open alerts but not acknowledged ones, resolved alerts
  are not reopened in the same window, a later window opens a new alert,
  illegal transitions rejected, history retained, and the synthetic
  incident breaching only `initech`.
- `test_api.py` (5) — pinned health/adoption responses, cross-tenant
  reads rejected (403) and missing header (401), the full alert flow over
  HTTP including cross-tenant action attempts (403), double-ack and
  double-resolve (409), unknown alert (404), and tenant-scoped
  evaluation.
- `test_generator.py` (4) — determinism (two runs byte-identical), shape,
  seed sensitivity, fixture determinism.

## Scope honesty

- All data is synthetic and generated in code. There is no real customer
  data, no real usage feed and no external service involved.
- This is a backend only: a JSON API plus the demo bootstrap above. There
  is no frontend, no charting UI and no authentication beyond the
  documented `X-Tenant-Id` scoping header, which is a demo stand-in for a
  real tenant-auth layer, not a security boundary for production.
- The health-score weights and component curves are a documented,
  deliberate choice for this demo, not a validated customer-success model.
- Storage is a single SQLite database; nothing here is scaled,
  replicated or hardened for production traffic.

## Layout

```
src/fde_health/
  clock.py      injected clock (system + fixed)
  models.py     Event, MonitorConfig, Alert, lifecycle transition table
  store.py      SQLite storage for events, alerts and alert history
  metrics.py    actives, adoption, trend, error rate, nearest-rank p95
  monitors.py   SLA evaluation, dedupe, auto-resolve rules
  health.py     the weighted health-score formula
  generator.py  deterministic synthetic + hand-computed fixture data
  app.py        FastAPI read/action API with tenant scoping
  serve.py      local demo server (seeded, clock pinned)
tests/          31 pytest tests
```
