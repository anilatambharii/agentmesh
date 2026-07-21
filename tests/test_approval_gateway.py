"""Unit tests for the human-in-the-loop approval gateway."""

from __future__ import annotations

import time

import pytest

from agentmesh.approval.gateway import (
    ApprovalGateway,
    ApprovalRule,
    ApprovalStatus,
)


def test_blanket_rule_matches_by_scope_alone():
    rule = ApprovalRule(name="wire-transfers", tool_patterns=["wire_transfer*"])
    assert rule.matches("finance", "wire_transfer_api", cost_usd=0.0, tokens=0)
    assert not rule.matches("finance", "search_docs", cost_usd=0.0, tokens=0)


def test_threshold_rule_requires_crossing_cost_or_tokens():
    rule = ApprovalRule(name="big-spend", min_cost_usd=5.0)
    assert not rule.matches("eng", "any_tool", cost_usd=4.99, tokens=0)
    assert rule.matches("eng", "any_tool", cost_usd=5.0, tokens=0)


def test_team_scoped_rule_ignores_other_teams():
    rule = ApprovalRule(name="finance-only", teams=["finance"], min_cost_usd=1.0)
    assert rule.matches("finance", "x", cost_usd=10.0, tokens=0)
    assert not rule.matches("engineering", "x", cost_usd=10.0, tokens=0)


def test_evaluate_returns_first_matching_rule():
    gateway = ApprovalGateway(rules=[
        ApprovalRule(name="cheap", min_cost_usd=1.0),
        ApprovalRule(name="blanket-payments", tool_patterns=["pay_*"]),
    ])
    decision = gateway.evaluate(team="eng", tool="pay_vendor", cost_usd=0.01, tokens=10)
    assert decision.requires_approval
    assert decision.rule.name == "blanket-payments"

    decision2 = gateway.evaluate(team="eng", tool="search", cost_usd=0.01, tokens=10)
    assert not decision2.requires_approval


def test_request_lifecycle_approve():
    gateway = ApprovalGateway(rules=[ApprovalRule(name="r", min_cost_usd=1.0)])
    req = gateway.request(team="finance", user="alice", tool="wire", description="test",
                           cost_usd=12.0, tokens=500)
    assert req.status == ApprovalStatus.PENDING
    assert gateway.pending() == [req]

    approved = gateway.approve(req.id, approved_by="bob", notes="looks fine")
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decided_by == "bob"
    assert gateway.pending() == []


def test_request_lifecycle_deny():
    gateway = ApprovalGateway(rules=[ApprovalRule(name="r")])
    req = gateway.request(team="eng", user="alice", tool="x", description="test")
    denied = gateway.deny(req.id, approved_by="bob", notes="too risky")
    assert denied.status == ApprovalStatus.DENIED


def test_cannot_resolve_twice():
    gateway = ApprovalGateway(rules=[ApprovalRule(name="r")])
    req = gateway.request(team="eng", user="alice", tool="x", description="test")
    gateway.approve(req.id)
    with pytest.raises(ValueError):
        gateway.deny(req.id)


def test_unknown_request_id_raises():
    gateway = ApprovalGateway(rules=[ApprovalRule(name="r")])
    with pytest.raises(ValueError):
        gateway.approve("APR-doesnotexist")


def test_timeout_defaults_to_deny():
    gateway = ApprovalGateway(rules=[ApprovalRule(name="r")], default_timeout_seconds=0)
    req = gateway.request(team="eng", user="alice", tool="x", description="test")
    time.sleep(0.05)
    resolved = gateway.get(req.id)
    assert resolved.status == ApprovalStatus.EXPIRED


def test_timeout_action_allow():
    gateway = ApprovalGateway(
        rules=[ApprovalRule(name="r")],
        default_timeout_seconds=0,
        default_timeout_action="allow",
    )
    req = gateway.request(team="eng", user="alice", tool="x", description="test")
    time.sleep(0.05)
    resolved = gateway.get(req.id)
    assert resolved.status == ApprovalStatus.APPROVED
    assert resolved.decided_by == "system (timeout)"


def test_alert_router_notified_on_request():
    calls = []

    class _FakeRouter:
        def alert(self, **kw):
            calls.append(kw)

    gateway = ApprovalGateway(rules=[ApprovalRule(name="r")], alert_router=_FakeRouter())
    gateway.request(team="finance", user="alice", tool="wire", description="big transfer",
                     cost_usd=99.0, tokens=100)
    assert len(calls) == 1
    assert calls[0]["team"] == "finance"


def test_summary_counts_by_status():
    gateway = ApprovalGateway(rules=[ApprovalRule(name="r")])
    r1 = gateway.request(team="eng", user="a", tool="x", description="1")
    r2 = gateway.request(team="eng", user="a", tool="x", description="2")
    gateway.approve(r1.id)
    gateway.deny(r2.id)
    summary = gateway.summary()
    assert summary["total"] == 2
    assert summary["approved"] == 1
    assert summary["denied"] == 1
    assert summary["pending"] == 0
