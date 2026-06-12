"""Tests for compliance report generation."""

import pytest
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy
from agentmesh.compliance.reporter import ComplianceReporter, FRAMEWORK_REQUIREMENTS


@pytest.fixture
def compliant_mesh():
    policy = Policy.from_yaml("""
policies:
  - name: compliant-policy
    budget:
      per_run_tokens: 50_000
      daily_tokens: 1_000_000
      monthly_usd: 3000
      hard_stop: true
    circuit_breaker:
      max_iterations: 25
    compliance:
      frameworks: [eu-ai-act, nist-ai-rmf, hipaa, soc2]
      pii_detection: true
""")
    mesh = AgentMesh(policy=policy, audit_signing_key=None)
    # Record some audit entries
    mesh.audit.record_call({"model": "claude-haiku-4-5"})
    mesh.audit.record_result({"result": "ok"})
    return mesh


@pytest.fixture
def minimal_mesh():
    policy = Policy.from_dict({
        "name": "minimal-policy",
        "budget": {"per_run_tokens": 10_000, "hard_stop": False},
    })
    return AgentMesh(policy=policy)


def test_all_frameworks_supported():
    supported = list(FRAMEWORK_REQUIREMENTS.keys())
    assert "eu-ai-act" in supported
    assert "nist-ai-rmf" in supported
    assert "hipaa" in supported
    assert "soc2" in supported
    assert "iso-42001" in supported


def test_eu_ai_act_report_generates(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="eu-ai-act")
    assert report.framework == "eu-ai-act"
    assert report.framework_name == "EU AI Act"
    assert len(report.checks) > 0


def test_nist_report_generates(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="nist-ai-rmf")
    assert report.framework == "nist-ai-rmf"
    assert len(report.checks) > 0


def test_hipaa_report_generates(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="hipaa")
    assert report.framework == "hipaa"
    assert len(report.checks) > 0


def test_soc2_report_generates(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="soc2")
    assert report.framework == "soc2"
    assert len(report.checks) > 0


def test_iso42001_report_generates(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="iso-42001")
    assert report.framework == "iso-42001"


def test_unknown_framework_raises(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    with pytest.raises(ValueError, match="Unknown framework"):
        reporter.generate(framework="made-up-standard")


def test_generate_all_returns_all_frameworks(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    all_reports = reporter.generate_all()
    assert set(all_reports.keys()) == set(FRAMEWORK_REQUIREMENTS.keys())


def test_hard_stop_check_passes_when_enabled(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="soc2")
    hard_stop_checks = [c for c in report.checks if c.check_id == "hard_stop_enabled"]
    if hard_stop_checks:
        assert hard_stop_checks[0].passed is True


def test_hard_stop_check_fails_when_disabled(minimal_mesh):
    reporter = ComplianceReporter(mesh=minimal_mesh)
    report = reporter.generate(framework="soc2")
    hard_stop_checks = [c for c in report.checks if c.check_id == "hard_stop_enabled"]
    if hard_stop_checks:
        assert hard_stop_checks[0].passed is False
        assert hard_stop_checks[0].remediation  # has remediation advice


def test_report_pass_rate(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="eu-ai-act")
    assert 0.0 <= report.pass_rate <= 1.0


def test_report_to_dict(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="eu-ai-act")
    d = report.to_dict()
    assert "framework" in d
    assert "pass_rate" in d
    assert "checks" in d
    assert "generated_at_iso" in d


def test_report_summary_string(compliant_mesh):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="eu-ai-act")
    summary = report.summary()
    assert "EU AI Act" in summary
    assert "compliant-policy" in summary


def test_report_save_and_load(compliant_mesh, tmp_path):
    reporter = ComplianceReporter(mesh=compliant_mesh)
    report = reporter.generate(framework="eu-ai-act")
    path = str(tmp_path / "report.json")
    report.save(path)

    import json
    with open(path) as f:
        data = json.load(f)
    assert data["framework"] == "eu-ai-act"
    assert "checks" in data
