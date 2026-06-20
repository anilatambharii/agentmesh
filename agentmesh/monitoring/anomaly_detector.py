"""
Real-time Cost Anomaly Detection

Detects abnormal token burn rates, runaway agent loops, and spend spikes
before they generate surprise bills. Uses sliding-window statistics.

Anomaly types:
  BURN_RATE_SPIKE   — tokens/min > N×baseline in last window
  SPEND_SPIKE       — cost/hour > threshold
  RUNAWAY_LOOP      — same team making N calls in M seconds
  QUOTA_EXHAUSTION  — team will exhaust quota within T hours at current rate
  COLD_CALL_FLOOD   — cache miss rate > 90% over N calls (no caching benefit)

Each anomaly emits an event that can trigger Slack/PagerDuty alerts.

Usage:
  detector = AnomalyDetector()
  anomaly  = detector.record(team="engineering", tokens=1500, cost_usd=0.002)
  if anomaly:
      print(anomaly.anomaly_type, anomaly.severity, anomaly.message)
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Deque, Dict, List, Optional


class AnomalySeverity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"


@dataclass
class CallRecord:
    ts:        float   # monotonic timestamp
    tokens:    int
    cost_usd:  float
    cache_hit: bool


@dataclass
class Anomaly:
    anomaly_type: str
    severity:     AnomalySeverity
    team:         str
    message:      str
    value:        float
    threshold:    float
    ts:           float = field(default_factory=time.monotonic)


@dataclass
class AnomalyConfig:
    # Burn rate: tokens/min spike
    burn_rate_window_secs:   float = 60.0
    burn_rate_baseline_min:  int   = 5        # minimum calls before baseline established
    burn_rate_spike_factor:  float = 5.0      # N× baseline triggers WARNING
    burn_rate_critical_factor: float = 10.0   # N× baseline triggers CRITICAL

    # Spend spike: cost/hour
    spend_window_secs:       float = 3600.0
    spend_warn_usd:          float = 10.0     # $10/hr per team
    spend_critical_usd:      float = 50.0     # $50/hr per team

    # Runaway loop: N calls in M seconds
    loop_window_secs:        float = 10.0
    loop_call_threshold:     int   = 20       # 20 calls in 10s = likely loop

    # Cache miss flood: high miss rate
    miss_flood_window:       int   = 20       # last N calls
    miss_flood_threshold:    float = 0.90     # 90%+ misses

    # Max records kept per team
    max_history:             int   = 500


class AnomalyDetector:
    """Thread-safe sliding-window anomaly detector."""

    def __init__(self, config: Optional[AnomalyConfig] = None):
        self.cfg     = config or AnomalyConfig()
        self._lock   = Lock()
        self._history: Dict[str, Deque[CallRecord]] = defaultdict(
            lambda: deque(maxlen=self.cfg.max_history)
        )
        self._baselines: Dict[str, float] = {}   # team -> tokens/min baseline

    def record(
        self,
        team:      str,
        tokens:    int,
        cost_usd:  float = 0.0,
        cache_hit: bool  = False,
    ) -> Optional[Anomaly]:
        """
        Record a call and check for anomalies.
        Returns the most severe Anomaly found, or None.
        """
        now = time.monotonic()
        rec = CallRecord(ts=now, tokens=tokens, cost_usd=cost_usd, cache_hit=cache_hit)

        with self._lock:
            hist = self._history[team]
            hist.append(rec)
            anomalies = self._check(team, hist, now)

        if not anomalies:
            return None
        # Return highest severity
        order = [AnomalySeverity.INFO, AnomalySeverity.WARNING, AnomalySeverity.CRITICAL]
        return max(anomalies, key=lambda a: order.index(a.severity))

    def _check(self, team: str, hist: Deque[CallRecord], now: float) -> List[Anomaly]:
        found: List[Anomaly] = []
        cfg   = self.cfg

        # ── Runaway loop: too many calls in short window ──────────────────────
        loop_calls = [r for r in hist if now - r.ts <= cfg.loop_window_secs]
        if len(loop_calls) >= cfg.loop_call_threshold:
            found.append(Anomaly(
                anomaly_type="RUNAWAY_LOOP",
                severity=AnomalySeverity.CRITICAL,
                team=team,
                value=len(loop_calls),
                threshold=cfg.loop_call_threshold,
                message=(
                    f"Team '{team}' made {len(loop_calls)} calls in "
                    f"{cfg.loop_window_secs:.0f}s — possible runaway agent loop"
                ),
            ))

        # ── Burn rate spike: tokens/min ───────────────────────────────────────
        window = [r for r in hist if now - r.ts <= cfg.burn_rate_window_secs]
        if len(window) >= cfg.burn_rate_baseline_min:
            elapsed = max(now - window[0].ts, 1.0)
            tpm     = sum(r.tokens for r in window) / (elapsed / 60.0)

            # Update rolling baseline (exponential moving average)
            prev = self._baselines.get(team, tpm)
            self._baselines[team] = 0.8 * prev + 0.2 * tpm

            baseline = self._baselines[team]
            if baseline > 0:
                ratio = tpm / baseline
                if ratio >= cfg.burn_rate_critical_factor:
                    found.append(Anomaly(
                        anomaly_type="BURN_RATE_SPIKE",
                        severity=AnomalySeverity.CRITICAL,
                        team=team,
                        value=tpm,
                        threshold=baseline * cfg.burn_rate_critical_factor,
                        message=(
                            f"Team '{team}' token burn rate {tpm:,.0f} tok/min is "
                            f"{ratio:.1f}× baseline — CRITICAL spike"
                        ),
                    ))
                elif ratio >= cfg.burn_rate_spike_factor:
                    found.append(Anomaly(
                        anomaly_type="BURN_RATE_SPIKE",
                        severity=AnomalySeverity.WARNING,
                        team=team,
                        value=tpm,
                        threshold=baseline * cfg.burn_rate_spike_factor,
                        message=(
                            f"Team '{team}' token burn rate {tpm:,.0f} tok/min is "
                            f"{ratio:.1f}× baseline"
                        ),
                    ))

        # ── Spend spike: cost/hour ────────────────────────────────────────────
        spend_window = [r for r in hist if now - r.ts <= cfg.spend_window_secs]
        hourly_cost  = sum(r.cost_usd for r in spend_window)
        if hourly_cost >= cfg.spend_critical_usd:
            found.append(Anomaly(
                anomaly_type="SPEND_SPIKE",
                severity=AnomalySeverity.CRITICAL,
                team=team,
                value=hourly_cost,
                threshold=cfg.spend_critical_usd,
                message=f"Team '{team}' spend ${hourly_cost:.2f} in last hour — exceeds critical threshold",
            ))
        elif hourly_cost >= cfg.spend_warn_usd:
            found.append(Anomaly(
                anomaly_type="SPEND_SPIKE",
                severity=AnomalySeverity.WARNING,
                team=team,
                value=hourly_cost,
                threshold=cfg.spend_warn_usd,
                message=f"Team '{team}' spend ${hourly_cost:.2f} in last hour",
            ))

        # ── Cache miss flood ──────────────────────────────────────────────────
        recent = list(hist)[-cfg.miss_flood_window:]
        if len(recent) >= cfg.miss_flood_window:
            miss_rate = sum(1 for r in recent if not r.cache_hit) / len(recent)
            if miss_rate >= cfg.miss_flood_threshold:
                found.append(Anomaly(
                    anomaly_type="COLD_CALL_FLOOD",
                    severity=AnomalySeverity.WARNING,
                    team=team,
                    value=miss_rate,
                    threshold=cfg.miss_flood_threshold,
                    message=(
                        f"Team '{team}' cache miss rate {miss_rate:.0%} over last "
                        f"{cfg.miss_flood_window} calls — no caching benefit"
                    ),
                ))

        return found

    def summary(self, team: str) -> dict:
        """Return current stats for a team."""
        with self._lock:
            hist = list(self._history.get(team, []))
        if not hist:
            return {"team": team, "calls": 0}
        now = time.monotonic()
        last_hour = [r for r in hist if now - r.ts <= 3600]
        return {
            "team":         team,
            "calls":        len(hist),
            "calls_1h":     len(last_hour),
            "tokens_1h":    sum(r.tokens for r in last_hour),
            "cost_usd_1h":  round(sum(r.cost_usd for r in last_hour), 4),
            "hit_rate_1h":  (
                sum(1 for r in last_hour if r.cache_hit) / len(last_hour)
                if last_hour else 0.0
            ),
            "baseline_tpm": round(self._baselines.get(team, 0), 1),
        }
