"""Tests for the EU AI Act readiness scanner."""

from __future__ import annotations

from agentmesh.compliance.readiness import ReadinessScanner, ENFORCEMENT_DATE
from agentmesh.core import AgentMesh
from agentmesh.policy.engine import Policy


def _mesh(**policy_overrides):
    base = {
        "name": "test-policy",
        "budget": {"per_run_tokens": 100_000, "hard_stop": True},
        "circuit_breaker": {"max_iterations": 10},
    }
    base.update(policy_overrides)
    return AgentMesh(policy=Policy.from_dict(base))


def test_unconfigured_policy_has_gaps():
    mesh = _mesh(budget={"per_run_tokens": 100_000, "hard_stop": False})
    report = ReadinessScanner(mesh=mesh).scan()

    assert not report.ready
    art14 = next(a for a in report.articles if a.article == "Article 14")
    assert not art14.passed
    assert any("hard_stop" in r for r in art14.remediation)


def test_fully_configured_mesh_passes_article_14_and_15_and_17():
    mesh = _mesh()
    mesh.injection_detector = object()
    mesh.pii_scanner = object()
    mesh.escalation_mgr = object()

    report = ReadinessScanner(mesh=mesh).scan()

    art17 = next(a for a in report.articles if a.article == "Article 17")
    assert art17.passed

    art15 = next(a for a in report.articles if a.article == "Article 15")
    assert art15.passed

    art14 = next(a for a in report.articles if a.article == "Article 14")
    assert art14.passed  # human_in_loop_supported (escalation_mgr) + hard_stop=True default


def test_penalty_tiers_are_not_uniform():
    """Regression guard: every article must cite the 15M/3% tier, not the
    35M/7% Article 5 ceiling — conflating them is the exact PR risk this
    scanner exists to avoid."""
    mesh = _mesh()
    report = ReadinessScanner(mesh=mesh).scan()
    for article in report.articles:
        assert "15M" in article.penalty_tier
        assert "35M" not in article.penalty_tier


def test_summary_and_json_round_trip():
    mesh = _mesh()
    report = ReadinessScanner(mesh=mesh).scan()

    text = report.summary()
    assert ENFORCEMENT_DATE in text
    assert "Article 12" in text

    d = report.to_dict()
    assert d["enforcement_date"] == ENFORCEMENT_DATE
    assert len(d["articles"]) == 4
