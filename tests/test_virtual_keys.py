"""Unit tests for per-agent virtual API keys."""

from __future__ import annotations

import json

from agentmesh.identity.keys import KEY_PREFIX, VirtualKeyManager


def test_create_returns_raw_key_once_and_it_resolves():
    manager = VirtualKeyManager()
    issued = manager.create(agent_id="triage-bot", team="engineering", tool="claude-code")

    assert issued.key.startswith(KEY_PREFIX)
    resolved = manager.resolve(issued.key)
    assert resolved is not None
    assert resolved.agent_id == "triage-bot"
    assert resolved.team == "engineering"


def test_raw_key_never_stored_only_hash():
    manager = VirtualKeyManager()
    issued = manager.create(agent_id="bot")
    record = manager.get(issued.record.key_id)
    assert record.key_hash != issued.key
    assert issued.key not in json.dumps(record.to_dict())


def test_unknown_key_does_not_resolve():
    manager = VirtualKeyManager()
    assert manager.resolve("amk_live_doesnotexist") is None
    assert manager.resolve("not-even-the-right-prefix") is None
    assert manager.resolve("") is None


def test_revoked_key_stops_resolving():
    manager = VirtualKeyManager()
    issued = manager.create(agent_id="bot")
    assert manager.resolve(issued.key) is not None

    manager.revoke(issued.record.key_id, reason="rotated")
    assert manager.resolve(issued.key) is None


def test_revoke_unknown_id_raises():
    manager = VirtualKeyManager()
    try:
        manager.revoke("vk_doesnotexist")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_scope_matching():
    manager = VirtualKeyManager()
    issued = manager.create(agent_id="bot", scopes=["claude-code", "cursor"])
    record = manager.resolve(issued.key)
    assert record.allows("claude-code")
    assert not record.allows("vscode-copilot")

    blanket = manager.create(agent_id="bot2", scopes=["*"])
    assert manager.resolve(blanket.key).allows("anything-at-all")


def test_list_filters_by_team_and_agent():
    manager = VirtualKeyManager()
    manager.create(agent_id="bot-a", team="engineering")
    manager.create(agent_id="bot-b", team="finance")
    manager.create(agent_id="bot-a", team="finance")

    assert len(manager.list(team="engineering")) == 1
    assert len(manager.list(agent_id="bot-a")) == 2
    assert len(manager.list()) == 3


def test_use_count_and_last_used_tracked():
    manager = VirtualKeyManager()
    issued = manager.create(agent_id="bot")
    manager.resolve(issued.key)
    manager.resolve(issued.key)
    record = manager.get(issued.record.key_id)
    assert record.use_count == 2
    assert record.last_used_at is not None


def test_persistence_across_manager_instances(tmp_path):
    store = tmp_path / "keys.json"
    manager1 = VirtualKeyManager(store_path=str(store))
    issued = manager1.create(agent_id="persistent-bot", team="engineering")

    manager2 = VirtualKeyManager(store_path=str(store))
    resolved = manager2.resolve(issued.key)
    assert resolved is not None
    assert resolved.agent_id == "persistent-bot"


def test_persisted_file_contains_no_raw_key(tmp_path):
    store = tmp_path / "keys.json"
    manager = VirtualKeyManager(store_path=str(store))
    issued = manager.create(agent_id="bot")

    raw_contents = store.read_text()
    assert issued.key not in raw_contents
