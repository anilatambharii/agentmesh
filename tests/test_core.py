"""Core AgentMesh tests."""

import pytest
from agentmesh import AgentMesh
from agentmesh.policy import Policy
from agentmesh.budget.enforcer import BudgetExceededError
from agentmesh.optimizer.circuit_breaker import CircuitBreakerError


@pytest.fixture
def policy():
    return Policy.from_dict({
        "name": "test-policy",
        "budget": {
            "per_run_tokens": 1000,
            "hard_stop": True,
        },
        "circuit_breaker": {
            "max_iterations": 3,
        },
    })


@pytest.fixture
def mesh(policy):
    return AgentMesh(policy=policy)


class MockResponse:
    class usage:
        input_tokens = 300
        output_tokens = 100
    model = "claude-haiku-4-5"


def test_budget_enforcer_tracks_usage(mesh):
    mesh.budget.reset_run()
    mesh.budget.check_pre_call({})
    mesh.budget.record_usage(MockResponse())
    assert mesh.budget.tokens_used == 400


def test_budget_hard_stop(mesh):
    mesh.budget.reset_run()
    # Exhaust budget
    for _ in range(3):
        mesh.budget.record_usage(MockResponse())
    with pytest.raises(BudgetExceededError):
        mesh.budget.check_pre_call({})


def test_circuit_breaker_trips(mesh):
    for _ in range(3):
        mesh.circuit_breaker.check()
        mesh.circuit_breaker.increment()
    with pytest.raises(CircuitBreakerError):
        mesh.circuit_breaker.check()


def test_audit_trail_chain_integrity(mesh):
    mesh.audit.record_call({"model": "claude-haiku-4-5"})
    mesh.audit.record_tool_call("web_search", "agent-1", {"query": "test"})
    mesh.audit.record_delegation("agent-1", "agent-2", "subtask")
    assert mesh.audit.verify() is True
    assert len(mesh.audit.entries) == 3


def test_policy_default():
    policy = Policy.default()
    assert policy.name == "agentmesh-default"
    assert policy.schema.budget.per_run_tokens == 200_000


def test_remaining_ratio(mesh):
    mesh.budget.reset_run()
    assert mesh.budget.remaining_ratio() == 1.0
    # Use 500 tokens (50% of 1000)
    class HalfBudgetResponse:
        class usage:
            input_tokens = 400
            output_tokens = 100
        model = "claude-haiku-4-5"
    mesh.budget.record_usage(HalfBudgetResponse())
    assert mesh.budget.remaining_ratio() == pytest.approx(0.5)


def test_stats(mesh):
    stats = mesh.stats
    assert "tokens_used" in stats
    assert "cost_usd" in stats
    assert "iterations" in stats
