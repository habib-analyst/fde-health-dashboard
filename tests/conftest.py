import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from fde_health.models import Event

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ev():
    """Event factory with sensible defaults."""

    def make(tenant="t1", user="u1", feature=None, etype="api_call", value=1.0, ts=NOW):
        return Event(
            tenant_id=tenant,
            user_id=user,
            feature=feature,
            event_type=etype,
            value=value,
            ts=ts,
        )

    return make
