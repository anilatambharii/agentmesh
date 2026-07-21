"""Integration test: virtual keys wired into the governance proxy."""

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
    defaults = dict(
        demo_mode=True,
        block_injections=False,
        toxicity_filter=False,
        anomaly_detection=False,
    )
    defaults.update(overrides)
    return TestClient(build_proxy_app(ProxyConfig(**defaults)))


def _issue_key(client: TestClient, **body) -> dict:
    resp = client.post("/v1/keys", json={"agent_id": "triage-bot", **body})
    assert resp.status_code == 200
    return resp.json()


def test_keys_disabled_by_default():
    client = _client()
    resp = client.post("/v1/keys", json={"agent_id": "bot"})
    assert resp.status_code == 404


def test_issued_key_authenticates_and_sets_identity():
    client = _client(virtual_keys_enabled=True)
    issued = _issue_key(client, team="engineering", tool="claude-code")

    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 50,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"Authorization": f"Bearer {issued['key']}"})

    assert resp.status_code == 200
    assert resp.headers["X-AgentMesh-Team"] == "engineering"
    assert resp.headers["X-AgentMesh-Agent-Id"] == "triage-bot"


def test_unknown_virtual_key_is_401():
    client = _client(virtual_keys_enabled=True)
    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 50,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"Authorization": "Bearer amk_live_doesnotexist"})
    assert resp.status_code == 401


def test_revoked_key_is_401():
    client = _client(virtual_keys_enabled=True)
    issued = _issue_key(client)
    revoke = client.post(f"/v1/keys/{issued['key_id']}/revoke", json={"reason": "test"})
    assert revoke.status_code == 200

    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 50,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"Authorization": f"Bearer {issued['key']}"})
    assert resp.status_code == 401


def test_out_of_scope_tool_is_403():
    client = _client(virtual_keys_enabled=True)
    issued = _issue_key(client, scopes=["cursor"], tool="vscode-copilot")

    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 50,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"Authorization": f"Bearer {issued['key']}"})
    assert resp.status_code == 403


def test_virtual_key_never_forwarded_as_vendor_credential(monkeypatch):
    """
    If the raw virtual key leaked through as the vendor API key, has_api_key()
    would report True (it's a non-empty, non-placeholder string) and the proxy
    would attempt a real call to Anthropic instead of falling back to the demo
    mock — which would hang/fail in a test sandbox with no network egress.
    Asserting the demo-mode header stays true is the regression guard for that.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _client(demo_mode=False, virtual_keys_enabled=True)
    issued = _issue_key(client)

    resp = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5", "max_tokens": 50,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"Authorization": f"Bearer {issued['key']}"})

    assert resp.status_code == 200
    assert resp.headers["X-AgentMesh-Demo"] == "true"


def test_list_and_revoke_endpoints():
    client = _client(virtual_keys_enabled=True)
    _issue_key(client, team="engineering")
    _issue_key(client, team="finance")

    listed = client.get("/v1/keys").json()["keys"]
    assert len(listed) == 2
    assert all("key" not in k for k in listed)  # never re-exposed after creation

    filtered = client.get("/v1/keys", params={"team": "finance"}).json()["keys"]
    assert len(filtered) == 1
