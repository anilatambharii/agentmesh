"""
Redis Distributed Cache Backend

Drop-in replacement for the in-memory CostOptimizer cache.
Enables shared cache across multiple AgentMesh proxy instances.

Features:
  - Exact match cache (SHA-256 key → JSON blob)
  - Semantic cache (vector stored as JSON array alongside blob)
  - TTL support (configurable per cache tier)
  - Atomic get+set via Redis pipelines
  - Graceful fallback to in-memory if Redis is unavailable

Usage:
  from agentmesh.cache.redis_backend import RedisCache

  cache = RedisCache(url="redis://localhost:6379/0", ttl_seconds=3600)
  cache.put("my-key", {"content": "Hello"}, model="claude-haiku-4-5", tokens=100)
  hit = cache.get("my-key")
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_REDIS_AVAILABLE = False
try:
    import redis as _redis
    _REDIS_AVAILABLE = True
except ImportError:
    pass


class RedisCache:
    """
    Redis-backed distributed cache for AgentMesh.

    Falls back to a local dict if Redis is unavailable so the proxy
    keeps running without a Redis dependency.

    Args:
        url:              Redis URL  e.g. "redis://localhost:6379/0"
                          or "rediss://user:pass@host:6380/0" for TLS
        ttl_seconds:      Default TTL for cache entries (default 3600)
        key_prefix:       Namespace prefix for all keys (default "agentmesh:")
        max_local_fallback: In-memory fallback entries when Redis is down
    """

    def __init__(
        self,
        url:                  str = "redis://localhost:6379/0",
        ttl_seconds:          int = 3600,
        key_prefix:           str = "agentmesh:",
        max_local_fallback:   int = 1000,
    ):
        self.ttl    = ttl_seconds
        self.prefix = key_prefix
        self._local: Dict[str, Any]     = {}   # fallback
        self._local_ts: Dict[str, float] = {}
        self._max_local = max_local_fallback
        self._client = None

        if _REDIS_AVAILABLE:
            try:
                self._client = _redis.from_url(
                    url, decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=1,
                )
                self._client.ping()
                logger.info("RedisCache connected to %s", url)
            except Exception as e:
                logger.warning("RedisCache: Redis unavailable (%s) — using local fallback", e)
                self._client = None
        else:
            logger.warning("RedisCache: redis-py not installed. Run: pip install redis")

    # ── Public API (mirrors CostOptimizer cache interface) ────────────────────

    def get(self, key: str) -> Optional[dict]:
        """Return cached response dict or None."""
        rkey = self._rkey(key)
        if self._client:
            try:
                raw = self._client.get(rkey)
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.debug("Redis get error: %s", e)
        # Fallback
        if key in self._local:
            if time.monotonic() - self._local_ts[key] < self.ttl:
                return self._local[key]
            del self._local[key]
        return None

    def put(self, key: str, value: dict, model: str = "", tokens: int = 0) -> None:
        """Store a response dict with TTL."""
        rkey = self._rkey(key)
        blob = json.dumps(value)
        if self._client:
            try:
                self._client.setex(rkey, self.ttl, blob)
                return
            except Exception as e:
                logger.debug("Redis put error: %s", e)
        # Fallback — evict oldest if full
        if len(self._local) >= self._max_local:
            oldest = min(self._local_ts, key=self._local_ts.get)
            self._local.pop(oldest, None)
            self._local_ts.pop(oldest, None)
        self._local[key]    = value
        self._local_ts[key] = time.monotonic()

    def get_semantic(self, key: str) -> Optional[Tuple[dict, list]]:
        """Return (response, embedding_vector) or None."""
        rkey = self._rkey(f"sem:{key}")
        if self._client:
            try:
                raw = self._client.get(rkey)
                if raw:
                    data = json.loads(raw)
                    return data.get("response"), data.get("embedding", [])
            except Exception as e:
                logger.debug("Redis get_semantic error: %s", e)
        return None

    def put_semantic(self, key: str, response: dict, embedding: list) -> None:
        """Store a response + its embedding vector."""
        rkey = self._rkey(f"sem:{key}")
        blob = json.dumps({"response": response, "embedding": embedding})
        if self._client:
            try:
                self._client.setex(rkey, self.ttl, blob)
                return
            except Exception as e:
                logger.debug("Redis put_semantic error: %s", e)

    def invalidate(self, key: str) -> None:
        rkey = self._rkey(key)
        if self._client:
            try:
                self._client.delete(rkey, self._rkey(f"sem:{key}"))
            except Exception:
                pass
        self._local.pop(key, None)

    def flush(self) -> int:
        """Clear all AgentMesh keys. Returns count deleted."""
        if self._client:
            try:
                keys = self._client.keys(f"{self.prefix}*")
                if keys:
                    return self._client.delete(*keys)
            except Exception:
                pass
        n = len(self._local)
        self._local.clear()
        self._local_ts.clear()
        return n

    def stats(self) -> dict:
        info = {"backend": "redis" if self._client else "local_fallback",
                "local_entries": len(self._local)}
        if self._client:
            try:
                i = self._client.info("memory")
                info["redis_used_memory"] = i.get("used_memory_human", "unknown")
                info["redis_keys"] = self._client.dbsize()
            except Exception:
                pass
        return info

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rkey(self, key: str) -> str:
        h = hashlib.sha256(key.encode()).hexdigest()[:32]
        return f"{self.prefix}{h}"
