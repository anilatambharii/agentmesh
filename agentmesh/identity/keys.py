"""
Per-Agent Identity — Virtual API Keys

Real vendor keys (Anthropic, OpenAI, Google, ...) live only on the proxy —
agents never see them and can never leak them. Each agent, tool, or team
instead authenticates with its own virtual key: individually revocable,
individually scoped, individually attributable in the audit trail and
chargeback reports.

This is the fix for the shared-credential pattern where every agent uses
the same vendor API key — no accountability, no way to revoke one agent
without breaking all of them, no way to tell which agent did what.

Usage:
    from agentmesh.identity.keys import VirtualKeyManager

    manager = VirtualKeyManager()
    issued = manager.create(agent_id="nightly-triage-bot", team="engineering",
                            tool="claude-code", scopes=["*"])
    print(issued.key)   # amk_live_xxxx... — shown once, never recoverable

    record = manager.resolve(issued.key)   # None if unknown/revoked
    manager.revoke(issued.record.key_id, reason="rotated")
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

KEY_PREFIX = "amk_live_"


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


@dataclass
class VirtualKey:
    key_id:         str
    key_hash:       str
    agent_id:       str
    team:           str            = ""
    user:           str            = ""
    tool:           str            = ""
    scopes:         List[str]      = field(default_factory=lambda: ["*"])
    description:    str            = ""
    created_at:     float          = field(default_factory=time.time)
    revoked:        bool           = False
    revoked_at:     Optional[float] = None
    revoked_reason: str            = ""
    last_used_at:   Optional[float] = None
    use_count:      int            = 0

    def allows(self, tool: str) -> bool:
        return any(fnmatch.fnmatch(tool or "", s) for s in self.scopes)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("key_hash", None)  # never surface the hash — no legitimate consumer needs it
        return d


@dataclass
class IssuedKey:
    """Returned only at creation time — the one moment the raw key exists in memory."""
    key:    str
    record: VirtualKey


class VirtualKeyManager:
    """
    Issues, resolves, and revokes per-agent virtual API keys.

    Keys are stored hashed (SHA-256), the same principle as password
    storage — a key that's lost cannot be recovered, only revoked and
    reissued. The proxy should never be a place a stolen credential can be
    read back out of.

    Args:
        store_path: Optional JSON file to persist key records (hashes only,
                    never raw keys) across restarts. None = in-memory only,
                    which means every issued key is invalidated on restart.
    """

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = Path(store_path) if store_path else None
        self._keys: Dict[str, VirtualKey] = {}   # key_id -> record
        self._by_hash: Dict[str, str] = {}       # key_hash -> key_id
        self._lock = threading.Lock()
        if self.store_path and self.store_path.exists():
            self._load()

    def create(
        self,
        agent_id: str,
        team: str = "",
        user: str = "",
        tool: str = "",
        scopes: Optional[List[str]] = None,
        description: str = "",
    ) -> IssuedKey:
        raw = KEY_PREFIX + secrets.token_hex(24)
        record = VirtualKey(
            key_id=f"vk_{secrets.token_hex(6)}",
            key_hash=_hash_key(raw),
            agent_id=agent_id, team=team, user=user, tool=tool,
            scopes=scopes or ["*"], description=description,
        )
        with self._lock:
            self._keys[record.key_id] = record
            self._by_hash[record.key_hash] = record.key_id
            self._save()
        return IssuedKey(key=raw, record=record)

    def resolve(self, raw_key: str) -> Optional[VirtualKey]:
        """Look up a presented key. Returns None if unknown, malformed, or revoked."""
        if not raw_key or not raw_key.startswith(KEY_PREFIX):
            return None
        h = _hash_key(raw_key)
        with self._lock:
            key_id = self._by_hash.get(h)
            if not key_id:
                return None
            record = self._keys.get(key_id)
            if not record or record.revoked:
                return None
            record.last_used_at = time.time()
            record.use_count += 1
            self._save()
            return record

    def revoke(self, key_id: str, reason: str = "") -> VirtualKey:
        with self._lock:
            record = self._keys.get(key_id)
            if not record:
                raise ValueError(f"Unknown virtual key: {key_id}")
            record.revoked = True
            record.revoked_at = time.time()
            record.revoked_reason = reason
            self._save()
            return record

    def get(self, key_id: str) -> Optional[VirtualKey]:
        with self._lock:
            return self._keys.get(key_id)

    def list(self, team: Optional[str] = None, agent_id: Optional[str] = None) -> List[VirtualKey]:
        with self._lock:
            records = list(self._keys.values())
        if team:
            records = [r for r in records if r.team == team]
        if agent_id:
            records = [r for r in records if r.agent_id == agent_id]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    # ── Persistence (hashes only — raw keys are never written to disk) ──────

    def _save(self) -> None:
        if not self.store_path:
            return
        payload = [asdict(r) for r in self._keys.values()]
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "w") as f:
            json.dump(payload, f, indent=2)

    def _load(self) -> None:
        with open(self.store_path) as f:
            payload = json.load(f)
        for item in payload:
            record = VirtualKey(**item)
            self._keys[record.key_id] = record
            self._by_hash[record.key_hash] = record.key_id
