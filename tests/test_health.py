"""Health score: pinned fixture values + component formula edges."""

from fde_health.generator import FIXTURE_NOW, build_fixture_events
from fde_health.health import compute_health, error_score, latency_score, trend_score

FIXTURES = build_fixture_events()


def test_health_alpha_pinned():
    result = compute_health(FIXTURES, "fixture-alpha", FIXTURE_NOW)
    # 0.35*62.5 + 0.25*70 + 0.20*0 + 0.20*90 = 57.375 -> 57.38
    assert result["score"] == 57.38
    assert result["components"] == {
        "adoption_score": 62.5,
        "trend_score": 70.0,
        "error_score": 0.0,
        "latency_score": 90.0,
    }
    assert result["inputs"]["trend_delta_pct"] == 20.0
    assert result["inputs"]["error_rate_pct"] == 20.0
    assert result["inputs"]["p95_latency_ms"] == 300.0
    assert result["inputs"]["dau"] == 2
    assert result["inputs"]["wau"] == 4
    assert result["inputs"]["mau"] == 5


def test_health_beta_pinned():
    result = compute_health(FIXTURES, "fixture-beta", FIXTURE_NOW)
    # 0.35*75 + 0.25*0 + 0.20*100 + 0.20*100 = 66.25
    assert result["score"] == 66.25
    assert result["components"] == {
        "adoption_score": 75.0,
        "trend_score": 0.0,
        "error_score": 100.0,
        "latency_score": 100.0,
    }


def test_component_formula_edges():
    assert trend_score(0.0) == 50.0
    assert trend_score(80.0) == 100.0  # clamped
    assert trend_score(-80.0) == 0.0  # clamped
    assert error_score(0.0) == 100.0
    assert error_score(10.0) == 50.0
    assert error_score(40.0) == 0.0  # clamped
    assert latency_score(200.0) == 100.0
    assert latency_score(700.0) == 50.0
    assert latency_score(5000.0) == 0.0  # clamped


def test_health_score_always_within_0_100():
    for tenant in ("fixture-alpha", "fixture-beta", "no-such-tenant"):
        result = compute_health(FIXTURES, tenant, FIXTURE_NOW)
        assert 0.0 <= result["score"] <= 100.0
