"""
Human-in-the-Loop Approval Gateway

The architectural fix for "an agent that can move $30M without a human
check is not a rogue agent — it's a correctly functioning agent with a
catastrophic job description." This gateway lets a policy mark specific
teams/tools/cost-tiers as requiring a human decision before the call
proceeds, enforced at the proxy — not left to the agent to request review.

The call does not block a request thread waiting on a human. Instead:
  1. A high-impact call is intercepted; a PENDING ApprovalRequest is filed
     and an alert is dispatched (Slack/PagerDuty/webhook).
  2. The caller receives a 202 with the approval id and is expected to
     resubmit once approved (X-AgentMesh-Approval-Id header).
  3. A human — or another system — calls approve()/deny() (CLI, dashboard,
     or Slack button webhook), which is recorded in the audit trail.
  4. Requests untouched past `timeout_seconds` resolve to `timeout_action`
     ("deny" by default — fail closed, not open).

Usage:
    gateway = ApprovalGateway(rules=[
        ApprovalRule(name="high-cost-calls", min_cost_usd=5.00),
        ApprovalRule(name="payments-tools", tool_patterns=["wire_transfer*", "send_payment*"]),
    ])
    decision = gateway.evaluate(team="finance", tool="wire_transfer_api", cost_usd=12.50)
    if decision.requires_approval:
        req = gateway.request(team="finance", user="alice", tool="wire_transfer_api",
                               description="...", cost_usd=12.50, rule=decision.rule)
        # ... later, from a human:
        gateway.approve(req.id, approved_by="bob@company.com")
"""

from __future__ import annotations

import fnmatch
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class ApprovalStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    DENIED   = "denied"
    EXPIRED  = "expired"


@dataclass
class ApprovalRule:
    name:           str            = ""
    teams:          List[str]      = field(default_factory=list)   # empty = all teams
    tool_patterns:  List[str]      = field(default_factory=list)   # glob patterns; empty = all tools
    min_cost_usd:   Optional[float] = None
    min_tokens:     Optional[int]   = None

    def matches(self, team: str, tool: str, cost_usd: float, tokens: int) -> bool:
        if self.teams and team not in self.teams:
            return False
        if self.tool_patterns and not any(fnmatch.fnmatch(tool or "", p) for p in self.tool_patterns):
            return False
        if self.min_cost_usd is None and self.min_tokens is None:
            return True  # blanket rule — scope match alone is enough
        cost_hit  = self.min_cost_usd is not None and cost_usd >= self.min_cost_usd
        token_hit = self.min_tokens   is not None and tokens   >= self.min_tokens
        return cost_hit or token_hit


@dataclass
class ApprovalDecision:
    requires_approval: bool
    rule: Optional[ApprovalRule] = None
    reason: str = ""


@dataclass
class ApprovalRequest:
    id:              str
    team:            str
    user:            str
    tool:            str
    description:     str
    cost_usd:        float
    tokens:          int
    rule_name:       str
    created_at:      float
    timeout_seconds: int
    timeout_action:  str
    status:          ApprovalStatus = ApprovalStatus.PENDING
    decided_by:      str            = ""
    decided_at:      Optional[float] = None
    notes:           str            = ""

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def is_timed_out(self) -> bool:
        return self.status == ApprovalStatus.PENDING and self.age_seconds > self.timeout_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":               self.id,
            "team":             self.team,
            "user":             self.user,
            "tool":             self.tool,
            "description":      self.description,
            "cost_usd":         round(self.cost_usd, 6),
            "tokens":           self.tokens,
            "rule":             self.rule_name,
            "status":           self.status.value,
            "age_seconds":      round(self.age_seconds, 1),
            "timeout_seconds":  self.timeout_seconds,
            "decided_by":       self.decided_by,
            "notes":            self.notes,
            "created_at":       time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
        }


class ApprovalGateway:
    """
    Evaluates governed calls against approval rules and manages the full
    lifecycle of resulting ApprovalRequests. Thread-safe, in-memory —
    for multi-instance deployments, front it with the same Redis backend
    used for the semantic cache (out of scope here; single-instance is the
    common case for the proxy today).

    Args:
        rules:             Ordered list of ApprovalRule — first match wins
        alert_router:      Optional AlertRouter to notify approvers
        default_timeout_seconds: Seconds before a PENDING request auto-resolves
        default_timeout_action:  "deny" (fail closed) or "allow" (fail open)
        on_new_request:    Optional callback(ApprovalRequest) fired on request()
    """

    def __init__(
        self,
        rules: Optional[List[ApprovalRule]] = None,
        alert_router: Optional[Any] = None,
        default_timeout_seconds: int = 900,
        default_timeout_action: str = "deny",
        on_new_request: Optional[Callable[[ApprovalRequest], None]] = None,
    ):
        self.rules = rules or []
        self.alert_router = alert_router
        self.default_timeout_seconds = default_timeout_seconds
        self.default_timeout_action = default_timeout_action
        self.on_new_request = on_new_request

        self._requests: Dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def evaluate(self, team: str, tool: str, cost_usd: float = 0.0, tokens: int = 0) -> ApprovalDecision:
        """Return whether this call needs a human decision before proceeding."""
        for rule in self.rules:
            if rule.matches(team, tool, cost_usd, tokens):
                return ApprovalDecision(
                    requires_approval=True, rule=rule,
                    reason=f"Matched approval rule '{rule.name or '(unnamed)'}'",
                )
        return ApprovalDecision(requires_approval=False)

    def request(
        self,
        team: str,
        user: str,
        tool: str,
        description: str,
        cost_usd: float = 0.0,
        tokens: int = 0,
        rule: Optional[ApprovalRule] = None,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            id=f"APR-{uuid.uuid4().hex[:8]}",
            team=team, user=user, tool=tool, description=description,
            cost_usd=cost_usd, tokens=tokens,
            rule_name=(rule.name if rule else ""),
            created_at=time.time(),
            timeout_seconds=self.default_timeout_seconds,
            timeout_action=self.default_timeout_action,
        )
        with self._lock:
            self._requests[req.id] = req

        if self.alert_router:
            self.alert_router.alert(
                title=f"Approval required: {req.id}",
                message=f"{description} — approve via POST /v1/approvals/{req.id}/approve "
                        f"or deny via POST /v1/approvals/{req.id}/deny",
                severity="warning", team=team,
                tool=tool, cost_usd=round(cost_usd, 4), tokens=tokens,
            )

        if self.on_new_request:
            try:
                self.on_new_request(req)
            except Exception:
                pass

        return req

    def approve(self, request_id: str, approved_by: str = "admin", notes: str = "") -> ApprovalRequest:
        return self._resolve(request_id, ApprovalStatus.APPROVED, approved_by, notes)

    def deny(self, request_id: str, approved_by: str = "admin", notes: str = "") -> ApprovalRequest:
        return self._resolve(request_id, ApprovalStatus.DENIED, approved_by, notes)

    def _resolve(self, request_id: str, status: ApprovalStatus, decided_by: str, notes: str) -> ApprovalRequest:
        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                raise ValueError(f"Unknown approval request {request_id}")
            if req.status != ApprovalStatus.PENDING:
                raise ValueError(f"Approval {request_id} already resolved: {req.status.value}")
            req.status = status
            req.decided_by = decided_by
            req.decided_at = time.time()
            req.notes = notes
            return req

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        """Look up a request, auto-resolving it to the timeout action if overdue."""
        with self._lock:
            req = self._requests.get(request_id)
            if req and req.is_timed_out:
                req.status = ApprovalStatus.EXPIRED if req.timeout_action != "allow" else ApprovalStatus.APPROVED
                req.decided_by = "system (timeout)"
                req.decided_at = time.time()
            return req

    def pending(self) -> List[ApprovalRequest]:
        with self._lock:
            for req in self._requests.values():
                if req.status == ApprovalStatus.PENDING and req.is_timed_out:
                    req.status = ApprovalStatus.EXPIRED if req.timeout_action != "allow" else ApprovalStatus.APPROVED
                    req.decided_by = "system (timeout)"
                    req.decided_at = time.time()
            return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]

    def all_requests(self) -> List[ApprovalRequest]:
        with self._lock:
            return sorted(self._requests.values(), key=lambda r: r.created_at, reverse=True)

    def summary(self) -> Dict[str, Any]:
        all_r = self.all_requests()
        return {
            "total":    len(all_r),
            "pending":  sum(1 for r in all_r if r.status == ApprovalStatus.PENDING),
            "approved": sum(1 for r in all_r if r.status == ApprovalStatus.APPROVED),
            "denied":   sum(1 for r in all_r if r.status == ApprovalStatus.DENIED),
            "expired":  sum(1 for r in all_r if r.status == ApprovalStatus.EXPIRED),
            "requests": [r.to_dict() for r in all_r],
        }
