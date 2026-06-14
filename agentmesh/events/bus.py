"""
AgentMesh Global Event Bus — real-time pub/sub for governance events.

Every governance decision (cache hit, quota warn, vendor route, escalation, LLM call)
is emitted here. The FastAPI SSE server subscribes and pushes events to any connected
dashboard or monitoring client — with zero latency, no polling.

Thread-safe. Multiple subscribers supported (one per browser tab / SSE connection).

Usage:
    from agentmesh.events.bus import get_bus, GovernanceEvent

    bus = get_bus()
    bus.emit(GovernanceEvent(kind="cache_hit", team="engineering", tokens_saved=450))

    # In a FastAPI SSE endpoint:
    q = bus.subscribe()
    try:
        while True:
            event = q.get(timeout=1)
            yield event.to_sse()
    finally:
        bus.unsubscribe(q)
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GovernanceEvent:
    """
    A single governance event emitted by AgentMesh during an LLM call.
    Carries enough context for the dashboard to display meaningful info.
    """
    kind: str                           # see KIND_* constants below
    timestamp: float                    = field(default_factory=time.time)
    agent_id: str                       = ""
    team: str                           = ""
    user: str                           = ""
    tool: str                           = ""
    model: str                          = ""
    vendor: str                         = ""
    tokens_used: int                    = 0
    tokens_saved: int                   = 0
    cost_usd: float                     = 0.0
    quota_pct: float                    = 0.0
    quota_used: int                     = 0
    quota_limit: int                    = 0
    escalation_id: str                  = ""
    cache_layer: str                    = ""   # "exact" | "semantic" | "miss"
    similarity: float                   = 0.0
    complexity_score: float             = 0.0
    message: str                        = ""
    meta: Dict[str, Any]                = field(default_factory=dict)

    # ── SSE serialization ───────────────────────────────────────────────────
    def to_sse(self) -> str:
        d = asdict(self)
        d["timestamp_iso"] = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"data: {json.dumps(d)}\n\n"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp_iso"] = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return d


# Event kind constants
KIND_LLM_CALL        = "llm_call"
KIND_CACHE_HIT       = "cache_hit"
KIND_CACHE_MISS      = "cache_miss"
KIND_QUOTA_WARN      = "quota_warn"
KIND_QUOTA_BLOCK     = "quota_block"
KIND_ESCALATION      = "escalation"
KIND_VENDOR_ROUTE    = "vendor_route"
KIND_CIRCUIT_BREAKER = "circuit_breaker"
KIND_BUDGET_WARN     = "budget_warn"
KIND_AUDIT           = "audit"
KIND_COMPRESS        = "compress"


class EventBus:
    """
    Thread-safe in-process event bus with bounded subscriber queues.

    Producers: call emit() from any thread.
    Consumers: call subscribe() → get a Queue → iterate with get(timeout=...).
               Call unsubscribe(q) when done to avoid memory leaks.

    History: last max_history events are kept for replaying to new SSE clients.
    """

    def __init__(self, max_history: int = 2000):
        self._subscribers: List[queue.Queue] = []
        self._lock        = threading.Lock()
        self._history:  List[GovernanceEvent] = []
        self._max_history = max_history

    def emit(self, event: GovernanceEvent) -> None:
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass  # drop for slow consumers rather than blocking

    def subscribe(self, maxsize: int = 1000) -> queue.Queue:
        """Register a new subscriber. Returns a Queue to read events from."""
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def history(self) -> List[GovernanceEvent]:
        with self._lock:
            return list(self._history)

    def recent(self, n: int = 50) -> List[GovernanceEvent]:
        with self._lock:
            return list(self._history[-n:])

    def stats_snapshot(self) -> Dict[str, Any]:
        """Quick aggregate of recent events for the /api/stats endpoint."""
        events = self.recent(500)
        kinds: Dict[str, int] = {}
        total_tokens = 0
        total_cost   = 0.0
        for e in events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
            total_tokens += e.tokens_used
            total_cost   += e.cost_usd
        calls = kinds.get(KIND_LLM_CALL, 0)
        hits  = kinds.get(KIND_CACHE_HIT, 0)
        return {
            "events_in_window": len(events),
            "by_kind":          kinds,
            "llm_calls":        calls,
            "cache_hits":       hits,
            "cache_misses":     kinds.get(KIND_CACHE_MISS, 0),
            "cache_hit_rate":   round(hits / max(hits + calls, 1), 3),
            "quota_warns":      kinds.get(KIND_QUOTA_WARN, 0),
            "quota_blocks":     kinds.get(KIND_QUOTA_BLOCK, 0),
            "escalations":      kinds.get(KIND_ESCALATION, 0),
            "vendor_routes":    kinds.get(KIND_VENDOR_ROUTE, 0),
            "total_tokens":     total_tokens,
            "total_cost_usd":   round(total_cost, 6),
            "subscribers":      self.subscriber_count,
        }

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


# ── Global singleton ──────────────────────────────────────────────────────────

_global_bus: Optional[EventBus] = None
_bus_lock   = threading.Lock()


def get_bus() -> EventBus:
    """Return the process-wide singleton EventBus. Creates it on first call."""
    global _global_bus
    with _bus_lock:
        if _global_bus is None:
            _global_bus = EventBus()
        return _global_bus


def reset_bus() -> None:
    """Clear history and drop all subscribers. Useful between test runs."""
    bus = get_bus()
    with bus._lock:
        bus._history.clear()
        bus._subscribers.clear()
