"""Run the demo API locally:  python3 -m fde_health.serve

Seeds the synthetic dataset, pins the clock at the fixture instant and
evaluates the default SLA monitors once, so /alerts already shows the
initech incident. Local only.
"""

from __future__ import annotations

import uvicorn

from .app import build_demo_app
from .monitors import DEFAULT_MONITORS, evaluate_monitors
from .generator import FIXTURE_NOW


def main() -> None:
    app = build_demo_app()
    evaluate_monitors(app.state.store, DEFAULT_MONITORS, FIXTURE_NOW)
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
