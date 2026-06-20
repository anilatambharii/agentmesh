"""
Vendor Health Monitor and Circuit Breaker

Tracks per-vendor error rates and latency. When a vendor degrades,
opens the circuit breaker so the MultiVendorRouter skips it automatically.

States: CLOSED (healthy) → OPEN (failed, reject fast) → HALF_OPEN (probe)

Usage:
  monitor = VendorHealthMonitor()
  monitor.record_success("anthropic", latency_ms=320)
  monitor.record_failure("openai", error="timeout")

  if not monitor.is_healthy("openai"):
      fallback = monitor.suggest_fallback("openai", available=["anthropic","google"])
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Deque, Dict, List, Optional


class CircuitState(str, Enum):
    CLOSED    = "closed"     # normal operation
    OPEN      = "open"       # failing — reject requests
    HALF_OPEN = "half_open"  # testing recovery


@dataclass
class _LatencyRecord:
    ts:         float
    latency_ms: float
    success:    bool


@dataclass
class VendorStats:
    vendor:       str
    state:        CircuitState = CircuitState.CLOSED
    total_calls:  int          = 0
    total_errors: int          = 0
    # Rolling window
    _window: Deque[_LatencyRecord] = field(default_factory=lambda: deque(maxlen=100))
    _state_changed_at: float = field(default_factory=time.monotonic)
    _half_open_probe_sent: bool = False

    @property
    def error_rate(self) -> float:
        if not self._window:
            return 0.0
        errors = sum(1 for r in self._window if not r.success)
        return errors / len(self._window)

    @property
    def p95_latency_ms(self) -> float:
        latencies = sorted(r.latency_ms for r in self._window if r.success)
        if not latencies:
            return 0.0
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]


@dataclass
class HealthConfig:
    # Circuit breaker thresholds
    error_rate_open_threshold:  float = 0.50   # open if 50%+ errors in window
    error_rate_close_threshold: float = 0.10   # close (recover) if <10% errors
    min_calls_for_open:         int   = 5      # need at least N calls before opening
    open_duration_secs:         float = 30.0   # stay OPEN for 30s before probing
    high_latency_ms:            float = 10_000 # 10s = degraded

    # Fallback priority (lower = preferred)
    vendor_priority: Dict[str, int] = field(default_factory=lambda: {
        "anthropic": 1,
        "openai":    2,
        "google":    3,
        "azure":     4,
        "mistral":   5,
        "cohere":    6,
    })


class VendorHealthMonitor:
    """
    Thread-safe circuit breaker and health tracker for LLM vendors.
    Integrates with MultiVendorRouter to skip degraded vendors.
    """

    def __init__(self, config: Optional[HealthConfig] = None):
        self.cfg   = config or HealthConfig()
        self._lock = Lock()
        self._stats: Dict[str, VendorStats] = {}

    def _get(self, vendor: str) -> VendorStats:
        if vendor not in self._stats:
            self._stats[vendor] = VendorStats(vendor=vendor)
        return self._stats[vendor]

    def record_success(self, vendor: str, latency_ms: float = 0.0) -> None:
        with self._lock:
            s = self._get(vendor)
            s.total_calls += 1
            s._window.append(_LatencyRecord(
                ts=time.monotonic(), latency_ms=latency_ms, success=True))
            self._maybe_close(s)

    def record_failure(self, vendor: str, error: str = "") -> None:
        with self._lock:
            s = self._get(vendor)
            s.total_calls  += 1
            s.total_errors += 1
            s._window.append(_LatencyRecord(
                ts=time.monotonic(), latency_ms=0.0, success=False))
            self._maybe_open(s)

    def is_healthy(self, vendor: str) -> bool:
        with self._lock:
            s = self._get(vendor)
            now = time.monotonic()

            if s.state == CircuitState.CLOSED:
                return True

            if s.state == CircuitState.OPEN:
                # Transition to HALF_OPEN after cooldown
                elapsed = now - s._state_changed_at
                if elapsed >= self.cfg.open_duration_secs:
                    s.state = CircuitState.HALF_OPEN
                    s._state_changed_at = now
                    s._half_open_probe_sent = False
                    return True   # allow one probe
                return False

            if s.state == CircuitState.HALF_OPEN:
                # Allow one probe request through
                if not s._half_open_probe_sent:
                    s._half_open_probe_sent = True
                    return True
                return False   # wait for probe result

        return True

    def suggest_fallback(self, failed_vendor: str, available: List[str]) -> Optional[str]:
        """Return the best healthy alternative vendor from the available list."""
        with self._lock:
            candidates = [
                v for v in available
                if v != failed_vendor and self._get(v).state == CircuitState.CLOSED
            ]
        if not candidates:
            return None
        priority = self.cfg.vendor_priority
        return min(candidates, key=lambda v: priority.get(v, 99))

    def health_report(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "vendor":       s.vendor,
                    "state":        s.state.value,
                    "error_rate":   round(s.error_rate, 3),
                    "p95_ms":       round(s.p95_latency_ms, 1),
                    "total_calls":  s.total_calls,
                    "total_errors": s.total_errors,
                }
                for s in self._stats.values()
            ]

    # ── Private helpers ───────────────────────────────────────────────────────

    def _maybe_open(self, s: VendorStats) -> None:
        if s.state != CircuitState.CLOSED:
            return
        if len(s._window) < self.cfg.min_calls_for_open:
            return
        if s.error_rate >= self.cfg.error_rate_open_threshold:
            s.state = CircuitState.OPEN
            s._state_changed_at = time.monotonic()

    def _maybe_close(self, s: VendorStats) -> None:
        if s.state not in (CircuitState.HALF_OPEN,):
            return
        if s.error_rate < self.cfg.error_rate_close_threshold:
            s.state = CircuitState.CLOSED
            s._state_changed_at = time.monotonic()
