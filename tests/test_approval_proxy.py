"""Integration test: the approval gate wired into the governance proxy."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentmesh.events.bus import reset_bus
from agentmesh.proxy.server import ProxyConfig, build_proxy_app


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_bus()
    yield
    reset_bus()


def _client(**overrides) -> TestClient:
    config = ProxyConfig(
        demo_mode=True,
        block_injections=False,
        toxicity_filter=False,
        anomaly_detection=False,
        **overrides,
    )
    return TestClient(build_proxy_app(config))


def test_no_approval_configured_calls_proceed_normally():
    client = _client()
    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert resp.status_code == 200
    assert "X-AgentMesh-Approval-Id" not in resp.headers


def test_gated_tool_call_pauses_for_approval_then_resumes():
    client = _client(approval_tools=["*"])  # blanket: every call requires approval

    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": "wire $1000 to acct 12345"}],
    }, headers={"X-AgentMesh-Team": "finance"})

    assert resp.status_code == 202
    body = resp.json()
    approval_id = body["approval_id"]
    assert approval_id

    # Not yet approved — the same request without the header still pends (new request)
    pending = client.get(f"/v1/approvals/{approval_id}")
    assert pending.json()["status"] == "pending"

    approved = client.post(f"/v1/approvals/{approval_id}/approve",
                            json={"approved_by": "cfo@company.com"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    resumed = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": "wire $1000 to acct 12345"}],
    }, headers={"X-AgentMesh-Team": "finance", "X-AgentMesh-Approval-Id": approval_id})

    assert resumed.status_code == 200
    assert resumed.headers["X-AgentMesh-Approval-Status"] == "approved"


def test_denied_approval_blocks_resubmission():
    client = _client(approval_tools=["*"])

    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": "delete production database"}],
    })
    approval_id = resp.json()["approval_id"]

    client.post(f"/v1/approvals/{approval_id}/deny", json={"approved_by": "security-team"})

    resumed = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": "delete production database"}],
    }, headers={"X-AgentMesh-Approval-Id": approval_id})

    assert resumed.status_code == 403


def test_unknown_approval_id_is_404():
    client = _client(approval_tools=["*"])
    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"X-AgentMesh-Approval-Id": "APR-doesnotexist"})
    assert resp.status_code == 404


def test_cost_threshold_rule_triggers_approval():
    # FAST tier pricing (~$0.80/1M in, $4/1M out) with max_tokens=1024 output
    # comfortably crosses a near-zero threshold on a real demo call.
    client = _client(approval_min_cost_usd=0.0001)
    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert resp.status_code == 202


def test_approvals_list_endpoint_without_config_is_404():
    client = _client()
    resp = client.get("/v1/approvals")
    assert resp.status_code == 404
