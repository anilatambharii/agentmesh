"""Tests for built-in policy templates."""

import pytest
from agentmesh.templates import load_template, list_templates, TEMPLATE_DIR
from agentmesh.policy.engine import Policy


AVAILABLE_TEMPLATES = ["fintech", "healthcare", "enterprise", "research", "customer_service",
                        "nvidia_nim", "eu_ai_act_high_risk"]


@pytest.mark.parametrize("template_name", AVAILABLE_TEMPLATES)
def test_template_loads(template_name):
    yaml_str = load_template(template_name)
    assert isinstance(yaml_str, str)
    assert len(yaml_str) > 100


@pytest.mark.parametrize("template_name", AVAILABLE_TEMPLATES)
def test_template_parses_as_valid_policy(template_name):
    yaml_str = load_template(template_name)
    policy = Policy.from_yaml(yaml_str)
    assert policy.name is not None
    assert len(policy.name) > 0


def test_template_fintech_has_hard_stop():
    policy = Policy.from_yaml(load_template("fintech"))
    assert policy.schema.budget.hard_stop is True


def test_template_healthcare_has_hipaa():
    from agentmesh.policy.schema import ComplianceFramework
    policy = Policy.from_yaml(load_template("healthcare"))
    frameworks = [f.value for f in policy.schema.compliance.frameworks]
    assert "hipaa" in frameworks


def test_template_healthcare_no_semantic_cache():
    policy = Policy.from_yaml(load_template("healthcare"))
    assert policy.schema.optimization.semantic_cache is False


def test_template_enterprise_has_soc2():
    policy = Policy.from_yaml(load_template("enterprise"))
    frameworks = [f.value for f in policy.schema.compliance.frameworks]
    assert "soc2" in frameworks


def test_template_research_allows_opus():
    policy = Policy.from_yaml(load_template("research"))
    assert "opus" in policy.schema.model_routing.max_allowed.lower()


def test_template_customer_service_tight_circuit_breaker():
    policy = Policy.from_yaml(load_template("customer_service"))
    assert policy.schema.circuit_breaker.max_iterations <= 15


def test_template_not_found():
    with pytest.raises(FileNotFoundError, match="not found"):
        load_template("nonexistent-template")


def test_template_dir_exists():
    assert TEMPLATE_DIR.exists()
    yamls = list(TEMPLATE_DIR.glob("*.yaml"))
    assert len(yamls) >= len(AVAILABLE_TEMPLATES)


def test_template_eu_ai_act_high_risk_has_approval_rules():
    policy = Policy.from_yaml(load_template("eu_ai_act_high_risk"))
    assert len(policy.schema.approval.rules) >= 1
    assert policy.schema.approval.timeout_action == "deny"
    frameworks = [f.value for f in policy.schema.compliance.frameworks]
    assert "eu-ai-act" in frameworks


def test_template_fintech_gates_payment_tools():
    policy = Policy.from_yaml(load_template("fintech"))
    patterns = [p for rule in policy.schema.approval.rules for p in rule.tool_patterns]
    assert any("wire_transfer" in p for p in patterns)


def test_list_templates_covers_every_bundled_pack():
    templates = list_templates()
    for name in AVAILABLE_TEMPLATES:
        assert name in templates
        assert templates[name]  # non-empty title parsed from the file header
