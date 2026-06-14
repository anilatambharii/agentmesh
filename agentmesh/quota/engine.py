"""
Enterprise Token Quota Engine — the governance layer that sits in front of every LLM call.

Every AI interaction in the company (VS Code Copilot, Teams bots, GitHub CI, Excel AI,
browser extensions, custom agents) flows through this quota check before a single token
is sent to any LLM vendor.

Quota hierarchy (most specific wins):
    user → team → project → tool → department → global

Example:
    policy = QuotaPolicy(
        global_monthly_tokens=10_000_000,
        team_monthly_tokens={"engineering": 2_000_000, "sales": 500_000},
        user_daily_tokens={"alice@co.com": 50_000},
        tool_monthly_tokens={"vscode-copilot": 1_000_000},
        warn_at_pct=0.80,
    )
    enforcer = QuotaEnforcer(policy)
    result = enforcer.check(QuotaIdentity(user="alice@co.com", team="engineering", tool="vscode-copilot"))
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class QuotaStatus(str, Enum):
    PASS    = "pass"
    WARN    = "warn"     # approaching limit — proceed but alert
    BLOCK   = "block"    # limit exceeded — reject call
    PENDING = "pending"  # escalation approved temporary grant


@dataclass
class QuotaIdentity:
    """Who is making the LLM call — resolved by AgentMesh from request headers / SDK context."""
    user:       str = "anonymous"
    team:       str = "default"
    project:    str = "default"
    tool:       str = "unknown"       # vscode-copilot, teams-bot, github-ci, excel, browser, etc.
    department: str = "default"
    session_id: str = ""


@dataclass
class QuotaCheckResult:
    status:            QuotaStatus
    identity:          QuotaIdentity
    limit_key:         str    # which dimension triggered (user/team/tool/global)
    used_tokens:       int
    limit_tokens:      int
    remaining_tokens:  int
    pct_used:          float
    message:           str    = ""
    escalation_id:     str    = ""   # set when an escalation was auto-filed


@dataclass
class QuotaPolicy:
    """
    Defines token limits across every dimension of the enterprise.

    All limits are in tokens (input + output combined).
    Periods: daily = rolling 24h, monthly = calendar month.
    """
    # Global (hard ceiling for the whole company)
    global_daily_tokens:   Optional[int] = None
    global_monthly_tokens: Optional[int] = None

    # Per-team overrides  {team_name: token_limit}
    team_daily_tokens:   Dict[str, int] = field(default_factory=dict)
    team_monthly_tokens: Dict[str, int] = field(default_factory=dict)

    # Per-user overrides  {email: token_limit}
    user_daily_tokens:   Dict[str, int] = field(default_factory=dict)
    user_monthly_tokens: Dict[str, int] = field(default_factory=dict)

    # Per-tool limits  {tool_name: monthly_token_limit}
    tool_monthly_tokens: Dict[str, int] = field(default_factory=dict)

    # Per-project limits  {project_name: monthly_token_limit}
    project_monthly_tokens: Dict[str, int] = field(default_factory=dict)

    # Behaviour thresholds
    warn_at_pct:       float = 0.80   # emit WARN when usage >= 80%
    hard_stop_at_pct:  float = 1.00   # emit BLOCK when usage >= 100%

    # Escalation
    auto_escalate:     bool  = True   # auto-file escalation request when blocked
    escalation_channel: str  = "in-memory"   # "jira" | "slack" | "email" | "servicenow" | "in-memory"
    temp_grant_tokens: int   = 100_000       # tokens to temporarily unlock pending approval


class QuotaStore:
    """
    Thread-safe token usage counter.

    Keyed by (dimension, key, period) e.g. ("team", "engineering", "2026-06").
    Pluggable: swap with Redis, Postgres, or any external store by subclassing.
    """

    def __init__(self):
        self._data: Dict[str, int] = {}
        self._lock = threading.Lock()

    def _period_key(self, period: str) -> str:
        """Return the current period string for a given period type."""
        t = time.localtime()
        if period == "daily":
            return f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        if period == "monthly":
            return f"{t.tm_year}-{t.tm_mon:02d}"
        return "all"

    def _key(self, dimension: str, key: str, period: str) -> str:
        return f"{dimension}:{key}:{self._period_key(period)}"

    def add(self, dimension: str, key: str, period: str, tokens: int) -> int:
        """Add tokens and return new total."""
        k = self._key(dimension, key, period)
        with self._lock:
            self._data[k] = self._data.get(k, 0) + tokens
            return self._data[k]

    def get(self, dimension: str, key: str, period: str) -> int:
        k = self._key(dimension, key, period)
        with self._lock:
            return self._data.get(k, 0)

    def set(self, dimension: str, key: str, period: str, tokens: int) -> None:
        k = self._key(dimension, key, period)
        with self._lock:
            self._data[k] = tokens

    def all_usage(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._data)

    def snapshot(self) -> List[Dict[str, Any]]:
        """Return a list of {dimension, key, period, used} for dashboard rendering."""
        with self._lock:
            rows = []
            for full_key, used in self._data.items():
                parts = full_key.split(":", 2)
                if len(parts) == 3:
                    rows.append({"dimension": parts[0], "key": parts[1], "period": parts[2], "used": used})
            return rows


class QuotaEnforcer:
    """
    Checks quota before allowing an LLM call to proceed.

    Sits at the front of AgentMesh's intercept pipeline — runs BEFORE cache, routing, or LLM.

    Usage:
        enforcer = QuotaEnforcer(policy)
        result = enforcer.check(identity)
        if result.status == QuotaStatus.BLOCK:
            raise QuotaExceededError(result.message)

        # ... call LLM ...
        enforcer.consume(identity, tokens_used)
    """

    # How long temp grants stay active (seconds) — default 24h
    TEMP_GRANT_TTL = 86_400

    def __init__(self, policy: QuotaPolicy, store: Optional[QuotaStore] = None):
        self.policy = policy
        self.store  = store or QuotaStore()
        # identity_key → (extra_tokens, expiry_timestamp)
        self._temp_grants: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _get_grant(self, dimension: str, key: str) -> int:
        """Return active temp grant tokens, expiring stale entries."""
        k = f"{dimension}:{key}"
        with self._lock:
            entry = self._temp_grants.get(k)
            if entry is None:
                return 0
            tokens, expiry = entry
            if time.time() > expiry:
                del self._temp_grants[k]
                return 0
            return tokens

    def check(self, identity: QuotaIdentity, estimated_tokens: int = 0) -> QuotaCheckResult:
        """
        Check whether this identity can make an LLM call.
        Returns QuotaCheckResult with PASS, WARN, or BLOCK.

        Changes vs original:
        - Collects ALL check results, returns most severe (fixes WARN shadowing BLOCK)
        - Pre-call: if estimated_tokens would push usage over hard limit, returns BLOCK early
        - Temp grants now expire after TEMP_GRANT_TTL seconds
        """
        self._evict_expired_grants()
        checks = self._build_checks(identity)

        worst_block: Optional[QuotaCheckResult] = None
        worst_warn:  Optional[QuotaCheckResult] = None

        for dimension, key, limit, period in checks:
            if limit is None:
                continue
            used            = self.store.get(dimension, key, period)
            extra           = self._get_grant(dimension, key)
            effective_limit = limit + extra
            remaining       = max(0, effective_limit - used)
            pct_used        = used / effective_limit if effective_limit else 0.0

            # Pre-call block: would this call push us over the hard limit?
            if estimated_tokens > 0 and (used + estimated_tokens) / effective_limit >= self.policy.hard_stop_at_pct:
                result = QuotaCheckResult(
                    status=QuotaStatus.BLOCK,
                    identity=identity,
                    limit_key=f"{dimension}:{key}",
                    used_tokens=used,
                    limit_tokens=effective_limit,
                    remaining_tokens=remaining,
                    pct_used=round(pct_used, 4),
                    message=(
                        f"Quota pre-block: {dimension} '{key}' would reach "
                        f"{used + estimated_tokens:,}/{effective_limit:,} tokens (estimated {estimated_tokens:,} needed)."
                    ),
                )
                if worst_block is None or result.pct_used > worst_block.pct_used:
                    worst_block = result
                continue

            if pct_used >= self.policy.hard_stop_at_pct:
                result = QuotaCheckResult(
                    status=QuotaStatus.BLOCK,
                    identity=identity,
                    limit_key=f"{dimension}:{key}",
                    used_tokens=used,
                    limit_tokens=effective_limit,
                    remaining_tokens=0,
                    pct_used=round(pct_used, 4),
                    message=f"Quota exceeded: {dimension} '{key}' used {used:,}/{effective_limit:,} tokens this {period}.",
                )
                if worst_block is None or result.pct_used > worst_block.pct_used:
                    worst_block = result

            elif pct_used >= self.policy.warn_at_pct:
                result = QuotaCheckResult(
                    status=QuotaStatus.WARN,
                    identity=identity,
                    limit_key=f"{dimension}:{key}",
                    used_tokens=used,
                    limit_tokens=effective_limit,
                    remaining_tokens=remaining,
                    pct_used=round(pct_used, 4),
                    message=f"Quota warning: {dimension} '{key}' at {pct_used:.0%} ({remaining:,} tokens remaining this {period}).",
                )
                if worst_warn is None or result.pct_used > worst_warn.pct_used:
                    worst_warn = result

        # Return most severe across ALL dimensions (block beats warn beats pass)
        if worst_block is not None:
            return worst_block
        if worst_warn is not None:
            return worst_warn

        tightest = self._tightest_check(identity)
        return QuotaCheckResult(
            status=QuotaStatus.PASS,
            identity=identity,
            limit_key=tightest[0],
            used_tokens=tightest[1],
            limit_tokens=tightest[2],
            remaining_tokens=max(0, tightest[2] - tightest[1]),
            pct_used=round(tightest[1] / tightest[2], 4) if tightest[2] else 0.0,
            message="Quota OK",
        )

    def consume(self, identity: QuotaIdentity, tokens: int) -> None:
        """Deduct tokens from all applicable quota dimensions."""
        checks = self._build_checks(identity)
        for dimension, key, limit, period in checks:
            if limit is not None:
                self.store.add(dimension, key, period, tokens)

    def grant_temporary(self, dimension: str, key: str, tokens: int,
                        ttl_seconds: Optional[int] = None) -> None:
        """Add a temporary token grant (e.g. pending escalation approval). Expires after ttl_seconds."""
        expiry = time.time() + (ttl_seconds or self.TEMP_GRANT_TTL)
        with self._lock:
            k = f"{dimension}:{key}"
            existing_tokens, existing_expiry = self._temp_grants.get(k, (0, 0.0))
            # Extend expiry if re-granting; add tokens
            self._temp_grants[k] = (existing_tokens + tokens, max(existing_expiry, expiry))

    def _evict_expired_grants(self) -> None:
        """Remove temp grants that have passed their TTL."""
        now = time.time()
        with self._lock:
            expired = [k for k, (_, exp) in self._temp_grants.items() if now > exp]
            for k in expired:
                del self._temp_grants[k]

    def usage_summary(self, identity: Optional[QuotaIdentity] = None) -> List[Dict[str, Any]]:
        """Return all usage rows, optionally filtered to a specific identity."""
        rows = self.store.snapshot()
        if identity:
            keys = {identity.user, identity.team, identity.project, identity.tool, identity.department, "global"}
            rows = [r for r in rows if r["key"] in keys or r["dimension"] == "global"]
        return rows

    # ── helpers ──────────────────────────────────────────────────────────────

    def _build_checks(self, identity: QuotaIdentity):
        """Return list of (dimension, key, limit, period) to check, most specific first."""
        p = self.policy
        return [
            ("user",       identity.user,       p.user_daily_tokens.get(identity.user),             "daily"),
            ("user",       identity.user,       p.user_monthly_tokens.get(identity.user),            "monthly"),
            ("team",       identity.team,       p.team_daily_tokens.get(identity.team),              "daily"),
            ("team",       identity.team,       p.team_monthly_tokens.get(identity.team),            "monthly"),
            ("tool",       identity.tool,       p.tool_monthly_tokens.get(identity.tool),            "monthly"),
            ("project",    identity.project,    p.project_monthly_tokens.get(identity.project),      "monthly"),
            ("global",     "global",            p.global_daily_tokens,                               "daily"),
            ("global",     "global",            p.global_monthly_tokens,                             "monthly"),
        ]

    def _tightest_check(self, identity: QuotaIdentity):
        """Find the dimension closest to its limit — used for PASS status reporting."""
        best = ("none", 0, 1, "daily")  # (dim:key, used, limit, period)
        best_pct = 0.0
        for dimension, key, limit, period in self._build_checks(identity):
            if limit is None:
                continue
            used = self.store.get(dimension, key, period)
            pct  = used / limit if limit else 0.0
            if pct > best_pct:
                best_pct = pct
                best = (f"{dimension}:{key}", used, limit, period)
        return best


class QuotaExceededError(Exception):
    def __init__(self, result: QuotaCheckResult):
        self.result = result
        super().__init__(result.message)
