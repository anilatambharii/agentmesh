"""
Token Escalation Engine — auto-file a request for more tokens when quota is exhausted.

When any team, user, or tool runs out of tokens, AgentMesh automatically creates an
escalation record and (optionally) sends it to your ticketing/approval system.

Integrations supported:
    - in-memory  (default, for testing and simple deployments)
    - slack       (POST to webhook URL)
    - email       (SMTP)
    - jira        (REST API — create issue in AGENTMESH project)
    - servicenow  (REST API — create incident)

Example:
    manager = EscalationManager(channel="slack", slack_webhook=os.environ["SLACK_WEBHOOK"])
    req = manager.request(
        identity=identity,
        quota_result=result,
        reason="Sprint 47 AI code review agents — need 2M extra tokens",
        requested_tokens=2_000_000,
    )
    print(req.id, req.status)   # ESC-0001, pending
    manager.approve(req.id)     # grants temp tokens in enforcer
"""

from __future__ import annotations

import time
import uuid
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from agentmesh.quota.engine import QuotaCheckResult, QuotaEnforcer, QuotaIdentity


class EscalationStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED  = "expired"


@dataclass
class EscalationRequest:
    id:               str
    identity:         QuotaIdentity
    quota_result:     QuotaCheckResult
    requested_tokens: int
    reason:           str
    priority:         str          # "low" | "medium" | "high" | "critical"
    created_at:       float
    status:           EscalationStatus = EscalationStatus.PENDING
    approver_email:   str               = ""
    approved_by:      str               = ""
    resolved_at:      Optional[float]   = None
    notes:            str               = ""
    ttl_hours:        int               = 72        # auto-expire if not actioned

    @property
    def age_hours(self) -> float:
        return (time.time() - self.created_at) / 3600

    @property
    def is_expired(self) -> bool:
        return self.status == EscalationStatus.PENDING and self.age_hours > self.ttl_hours

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":               self.id,
            "user":             self.identity.user,
            "team":             self.identity.team,
            "tool":             self.identity.tool,
            "limit_key":        self.quota_result.limit_key,
            "used_tokens":      self.quota_result.used_tokens,
            "limit_tokens":     self.quota_result.limit_tokens,
            "pct_used":         f"{self.quota_result.pct_used:.0%}",
            "requested_tokens": self.requested_tokens,
            "reason":           self.reason,
            "priority":         self.priority,
            "status":           self.status.value,
            "age_hours":        round(self.age_hours, 1),
            "approver":         self.approver_email,
            "approved_by":      self.approved_by,
            "notes":            self.notes,
            "created_at":       time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created_at)),
        }


_COUNTER = 0
_COUNTER_LOCK = threading.Lock()

def _next_id() -> str:
    global _COUNTER
    with _COUNTER_LOCK:
        _COUNTER += 1
        return f"ESC-{_COUNTER:04d}"


class EscalationManager:
    """
    Manages the full lifecycle of token quota escalation requests.

    Wired into QuotaEnforcer so that when a BLOCK is returned, an escalation is
    auto-filed and a temporary grant is optionally applied so the blocked call can
    proceed with a warning rather than a hard error (configurable).
    """

    def __init__(
        self,
        enforcer:          Optional[QuotaEnforcer]  = None,
        channel:           str                       = "in-memory",
        slack_webhook:     Optional[str]             = None,
        email_smtp:        Optional[Dict[str, str]]  = None,
        jira_config:       Optional[Dict[str, str]]  = None,
        auto_temp_grant:   bool                      = True,
        default_priority:  str                       = "medium",
        approver_email:    str                       = "it-licensing@company.com",
        on_new_request:    Optional[Callable]        = None,   # callback(EscalationRequest)
    ):
        self.enforcer        = enforcer
        self.channel         = channel
        self.slack_webhook   = slack_webhook
        self.email_smtp      = email_smtp
        self.jira_config     = jira_config
        self.auto_temp_grant = auto_temp_grant
        self.default_priority = default_priority
        self.approver_email  = approver_email
        self.on_new_request  = on_new_request

        self._requests: Dict[str, EscalationRequest] = {}
        self._lock = threading.Lock()

    def request(
        self,
        identity:          QuotaIdentity,
        quota_result:      QuotaCheckResult,
        requested_tokens:  int               = 500_000,
        reason:            str               = "Additional token capacity required",
        priority:          Optional[str]     = None,
        approver_email:    Optional[str]     = None,
        ttl_hours:         int               = 72,
    ) -> EscalationRequest:
        """
        File a new escalation request.  Returns immediately; delivery is async.
        """
        req = EscalationRequest(
            id=_next_id(),
            identity=identity,
            quota_result=quota_result,
            requested_tokens=requested_tokens,
            reason=reason,
            priority=priority or self.default_priority,
            created_at=time.time(),
            approver_email=approver_email or self.approver_email,
            ttl_hours=ttl_hours,
        )

        with self._lock:
            self._requests[req.id] = req

        # Apply a temporary grant so the blocked call can proceed while awaiting approval
        if self.auto_temp_grant and self.enforcer:
            dim, key = quota_result.limit_key.split(":", 1) if ":" in quota_result.limit_key else ("team", identity.team)
            self.enforcer.grant_temporary(dim, key, self.enforcer.policy.temp_grant_tokens)

        self._dispatch(req)

        if self.on_new_request:
            try:
                self.on_new_request(req)
            except Exception:
                pass

        return req

    def approve(self, request_id: str, approved_by: str = "admin", notes: str = "") -> EscalationRequest:
        """Approve a pending request — persists the token grant."""
        with self._lock:
            req = self._requests.get(request_id)
        if not req:
            raise ValueError(f"Unknown escalation {request_id}")
        req.status      = EscalationStatus.APPROVED
        req.approved_by = approved_by
        req.resolved_at = time.time()
        req.notes       = notes
        if self.enforcer:
            dim, key = req.quota_result.limit_key.split(":", 1) if ":" in req.quota_result.limit_key else ("team", req.identity.team)
            self.enforcer.grant_temporary(dim, key, req.requested_tokens)
        return req

    def reject(self, request_id: str, approved_by: str = "admin", notes: str = "") -> EscalationRequest:
        with self._lock:
            req = self._requests.get(request_id)
        if not req:
            raise ValueError(f"Unknown escalation {request_id}")
        req.status      = EscalationStatus.REJECTED
        req.approved_by = approved_by
        req.resolved_at = time.time()
        req.notes       = notes
        return req

    def get(self, request_id: str) -> Optional[EscalationRequest]:
        with self._lock:
            return self._requests.get(request_id)

    def pending(self) -> List[EscalationRequest]:
        with self._lock:
            return [r for r in self._requests.values() if r.status == EscalationStatus.PENDING and not r.is_expired]

    def all_requests(self) -> List[EscalationRequest]:
        with self._lock:
            return sorted(self._requests.values(), key=lambda r: r.created_at, reverse=True)

    def summary(self) -> Dict[str, Any]:
        all_r = self.all_requests()
        return {
            "total":    len(all_r),
            "pending":  sum(1 for r in all_r if r.status == EscalationStatus.PENDING),
            "approved": sum(1 for r in all_r if r.status == EscalationStatus.APPROVED),
            "rejected": sum(1 for r in all_r if r.status == EscalationStatus.REJECTED),
            "requests": [r.to_dict() for r in all_r],
        }

    # ── Dispatch to external channels ────────────────────────────────────────

    def _dispatch(self, req: EscalationRequest) -> None:
        if self.channel == "in-memory":
            return   # already stored in _requests

        if self.channel == "slack" and self.slack_webhook:
            self._send_slack(req)
        elif self.channel == "email" and self.email_smtp:
            self._send_email(req)
        elif self.channel == "jira" and self.jira_config:
            self._send_jira(req)

    def _send_slack(self, req: EscalationRequest) -> None:
        try:
            import urllib.request as ur, json
            payload = {
                "text": f":warning: *Token Quota Escalation {req.id}*",
                "attachments": [{
                    "color": "#FF6B35" if req.priority == "high" else "#FFA500",
                    "fields": [
                        {"title": "Team",      "value": req.identity.team,      "short": True},
                        {"title": "User",      "value": req.identity.user,      "short": True},
                        {"title": "Tool",      "value": req.identity.tool,      "short": True},
                        {"title": "Priority",  "value": req.priority,           "short": True},
                        {"title": "Requested", "value": f"{req.requested_tokens:,} tokens", "short": True},
                        {"title": "Reason",    "value": req.reason,             "short": False},
                    ],
                    "footer": f"AgentMesh | Approve: POST /quota/escalations/{req.id}/approve",
                }],
            }
            data = json.dumps(payload).encode()
            r = ur.Request(self.slack_webhook, data=data, headers={"Content-Type": "application/json"})
            ur.urlopen(r, timeout=5)
        except Exception:
            pass

    def _send_email(self, req: EscalationRequest) -> None:
        try:
            import smtplib, email.mime.text as emt
            cfg = self.email_smtp
            body = (
                f"Token Quota Escalation Request {req.id}\n\n"
                f"Team: {req.identity.team}\n"
                f"User: {req.identity.user}\n"
                f"Tool: {req.identity.tool}\n"
                f"Quota hit: {req.quota_result.limit_key} ({req.quota_result.pct_used:.0%} used)\n"
                f"Requested: {req.requested_tokens:,} additional tokens\n"
                f"Priority: {req.priority}\n"
                f"Reason: {req.reason}\n\n"
                f"Approve via AgentMesh CLI: agentmesh quota approve {req.id}\n"
            )
            msg = emt.MIMEText(body)
            msg["Subject"] = f"[AgentMesh] Token Quota Escalation {req.id} — {req.identity.team}"
            msg["From"]    = cfg.get("from", "agentmesh@company.com")
            msg["To"]      = req.approver_email
            with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587))) as s:
                if cfg.get("tls"):
                    s.starttls()
                if cfg.get("user"):
                    s.login(cfg["user"], cfg["password"])
                s.sendmail(msg["From"], [msg["To"]], msg.as_string())
        except Exception:
            pass

    def _send_jira(self, req: EscalationRequest) -> None:
        try:
            import urllib.request as ur, json, base64
            cfg = self.jira_config
            token = base64.b64encode(f"{cfg['user']}:{cfg['token']}".encode()).decode()
            payload = {
                "fields": {
                    "project":     {"key": cfg.get("project", "AGENTMESH")},
                    "summary":     f"Token Quota Escalation {req.id} — {req.identity.team}",
                    "description": {
                        "type":    "doc",
                        "version": 1,
                        "content": [{"type": "paragraph", "content": [
                            {"type": "text", "text": f"Team: {req.identity.team}\nUser: {req.identity.user}\nRequested: {req.requested_tokens:,} tokens\nReason: {req.reason}"}
                        ]}],
                    },
                    "issuetype":   {"name": "Task"},
                    "priority":    {"name": req.priority.capitalize()},
                    "labels":      ["agentmesh", "token-quota"],
                },
            }
            data = json.dumps(payload).encode()
            r = ur.Request(
                f"{cfg['base_url']}/rest/api/3/issue",
                data=data,
                headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
            )
            ur.urlopen(r, timeout=10)
        except Exception:
            pass
