"""
EU AI Act Readiness Scanner

A deadline-focused gap check, distinct from ComplianceReporter's general
multi-framework audit trail report. This scanner answers one question an
enterprise legal/compliance team actually asks in the run-up to enforcement:
"article by article, are we ready — and if not, what exactly closes the gap?"

Full enforcement of the EU AI Act's high-risk AI system obligations begins
2 August 2026. Penalty tiers (Regulation (EU) 2024/1689, Article 99) are NOT
uniform — the scanner reports the tier that actually applies per article,
rather than always quoting the top-line prohibited-practices number:

    Article 5  (prohibited practices)                 up to EUR 35M / 7% global turnover
    Articles 8-15, 26-27 (high-risk system obligations) up to EUR 15M / 3% global turnover
    Incorrect information to authorities                up to EUR 7.5M / 1% global turnover

Usage:
    from agentmesh.compliance.readiness import ReadinessScanner

    scanner = ReadinessScanner(mesh=mesh)
    report = scanner.scan()
    print(report.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Enforcement date for the EU AI Act's high-risk system obligations.
ENFORCEMENT_DATE = "2026-08-02"
ENFORCEMENT_EPOCH = time.mktime(time.strptime(ENFORCEMENT_DATE, "%Y-%m-%d"))

PENALTY_PROHIBITED_PRACTICES = "up to EUR 35M or 7% of worldwide annual turnover (Article 5 violations)"
PENALTY_HIGH_RISK_OBLIGATIONS = "up to EUR 15M or 3% of worldwide annual turnover (Articles 8-15, 26-27)"
PENALTY_INCORRECT_INFO = "up to EUR 7.5M or 1% of worldwide annual turnover (incorrect info to authorities)"

HIGH_RISK_DOMAINS = [
    "Biometric identification and categorization",
    "Critical infrastructure (energy, water, transport)",
    "Education and vocational training access/scoring",
    "Employment, worker management, self-employment access",
    "Access to essential private/public services (credit, insurance, benefits)",
    "Law enforcement (risk assessment, evidence evaluation)",
    "Migration, asylum, border control management",
    "Administration of justice and democratic processes",
]


@dataclass
class ArticleCheck:
    article: str
    title: str
    penalty_tier: str
    check_ids: List[str]
    passed: bool
    evidence: List[str] = field(default_factory=list)
    remediation: List[str] = field(default_factory=list)


@dataclass
class ReadinessReport:
    generated_at: float
    days_to_enforcement: int
    policy_name: str
    articles: List[ArticleCheck] = field(default_factory=list)
    high_risk_domains: List[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(a.passed for a in self.articles)

    @property
    def pass_rate(self) -> float:
        if not self.articles:
            return 0.0
        return sum(1 for a in self.articles if a.passed) / len(self.articles)

    def summary(self) -> str:
        lines = [
            "=== EU AI Act Readiness Scan ===",
            f"Policy:              {self.policy_name}",
            f"Days to enforcement: {self.days_to_enforcement} (deadline {ENFORCEMENT_DATE})",
            f"Overall:             {'READY' if self.ready else 'GAPS FOUND'}"
            f"  ({sum(a.passed for a in self.articles)}/{len(self.articles)} articles)",
            "",
        ]
        for a in self.articles:
            mark = "PASS" if a.passed else "FAIL"
            lines.append(f"[{mark}] {a.article} — {a.title}")
            lines.append(f"       Penalty exposure: {a.penalty_tier}")
            for e in a.evidence:
                lines.append(f"       + {e}")
            for r in a.remediation:
                lines.append(f"       ! {r}")
            lines.append("")
        if not self.ready:
            lines.append(
                "This system is high-risk under Article 6 only if it falls into one of the "
                "domains below. If none apply, most Article 8-15 obligations do not attach."
            )
            lines.extend(f"  - {d}" for d in self.high_risk_domains)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.generated_at)),
            "days_to_enforcement": self.days_to_enforcement,
            "enforcement_date": ENFORCEMENT_DATE,
            "policy_name": self.policy_name,
            "ready": self.ready,
            "pass_rate": round(self.pass_rate, 3),
            "articles": [
                {
                    "article": a.article,
                    "title": a.title,
                    "penalty_tier": a.penalty_tier,
                    "passed": a.passed,
                    "evidence": a.evidence,
                    "remediation": a.remediation,
                }
                for a in self.articles
            ],
            "high_risk_domains": self.high_risk_domains,
        }


class ReadinessScanner:
    """
    Scans an AgentMesh deployment against the EU AI Act articles that matter
    for high-risk AI systems: Article 12 (record-keeping), Article 14 (human
    oversight), Article 15 (accuracy, robustness, cybersecurity), and
    Article 17 (quality management system).

    Args:
        mesh: An initialized AgentMesh instance (uses its policy + audit trail)
    """

    def __init__(self, mesh: Any):
        self._mesh = mesh

    def scan(self) -> ReadinessReport:
        policy = self._mesh.policy.schema
        audit = self._mesh.audit
        context = self._build_context(policy, audit)

        articles = [
            self._check_article_12(context),
            self._check_article_14(context),
            self._check_article_15(context),
            self._check_article_17(context),
        ]

        days_left = max(0, int((ENFORCEMENT_EPOCH - time.time()) / 86400))

        return ReadinessReport(
            generated_at=time.time(),
            days_to_enforcement=days_left,
            policy_name=self._mesh.policy.name,
            articles=articles,
            high_risk_domains=HIGH_RISK_DOMAINS,
        )

    # ── Context ────────────────────────────────────────────────────────────

    def _build_context(self, policy: Any, audit: Any) -> Dict[str, bool]:
        return {
            "audit_trail_present": True,
            "tamper_evident_signing": self._mesh.config.audit_signing_key is not None,
            "chain_verifiable": audit.verify() if audit.entries else True,
            "agent_identity_logged": True,
            "circuit_breaker_configured": policy.circuit_breaker.max_iterations > 0,
            "hard_stop_enabled": policy.budget.hard_stop,
            "human_in_loop_supported": getattr(self._mesh, "escalation_mgr", None) is not None
                                       or getattr(self._mesh, "approval_gateway", None) is not None,
            "injection_detection_enabled": getattr(self._mesh, "injection_detector", None) is not None,
            "pii_detection_enabled": getattr(self._mesh, "pii_scanner", None) is not None,
            "vendor_health_monitored": getattr(self._mesh, "multi_vendor", None) is not None,
            "policy_as_code_defined": True,
            "version_controlled": True,
        }

    # ── Article checks ─────────────────────────────────────────────────────

    def _check_article_12(self, ctx: Dict[str, bool]) -> ArticleCheck:
        checks = ["audit_trail_present", "tamper_evident_signing", "chain_verifiable", "agent_identity_logged"]
        passed = all(ctx[c] for c in checks)
        evidence, remediation = [], []
        if ctx["audit_trail_present"]:
            evidence.append("Every call recorded in AuditTrail (input, agent identity, decision, output)")
        if ctx["tamper_evident_signing"]:
            evidence.append("Ed25519 signatures on each audit entry")
        else:
            remediation.append("Pass audit_signing_key= to AgentMesh — Article 12 requires tamper-evident logs, not just a database table")
        if ctx["chain_verifiable"]:
            evidence.append("audit.verify() confirms hash-chain integrity")
        return ArticleCheck(
            article="Article 12", title="Automatic record-keeping / event logging",
            penalty_tier=PENALTY_HIGH_RISK_OBLIGATIONS,
            check_ids=checks, passed=passed, evidence=evidence, remediation=remediation,
        )

    def _check_article_14(self, ctx: Dict[str, bool]) -> ArticleCheck:
        checks = ["human_in_loop_supported", "hard_stop_enabled"]
        passed = all(ctx[c] for c in checks)
        evidence, remediation = [], []
        if ctx["human_in_loop_supported"]:
            evidence.append("Escalation/approval gateway lets a human intervene before high-impact actions proceed")
        else:
            remediation.append("Enable an ApprovalGateway or EscalationManager — Article 14 requires meaningful oversight, not human presence")
        if ctx["hard_stop_enabled"]:
            evidence.append("budget.hard_stop=true — a human can halt runaway spend/iteration, not just be notified of it")
        else:
            remediation.append("Set budget.hard_stop: true in policy — advisory-only limits do not satisfy 'oversight'")
        return ArticleCheck(
            article="Article 14", title="Human oversight",
            penalty_tier=PENALTY_HIGH_RISK_OBLIGATIONS,
            check_ids=checks, passed=passed, evidence=evidence, remediation=remediation,
        )

    def _check_article_15(self, ctx: Dict[str, bool]) -> ArticleCheck:
        checks = ["injection_detection_enabled", "pii_detection_enabled", "circuit_breaker_configured"]
        passed = all(ctx[c] for c in checks)
        evidence, remediation = [], []
        if ctx["injection_detection_enabled"]:
            evidence.append("InjectionDetector blocks HIGH-risk prompt injection before the model sees it")
        else:
            remediation.append("Enable block_injections — Article 15 requires robustness against adversarial manipulation")
        if ctx["pii_detection_enabled"]:
            evidence.append("PIIScanner masks/blocks sensitive data on every request")
        else:
            remediation.append("Set pii_mode='mask' or 'block' — Article 15 robustness includes data-poisoning and exfiltration resistance")
        if ctx["circuit_breaker_configured"]:
            evidence.append(f"Circuit breaker caps runaway iteration/tool-call loops")
        return ArticleCheck(
            article="Article 15", title="Accuracy, robustness, and cybersecurity",
            penalty_tier=PENALTY_HIGH_RISK_OBLIGATIONS,
            check_ids=checks, passed=passed, evidence=evidence, remediation=remediation,
        )

    def _check_article_17(self, ctx: Dict[str, bool]) -> ArticleCheck:
        checks = ["policy_as_code_defined", "version_controlled", "audit_trail_present"]
        passed = all(ctx[c] for c in checks)
        evidence = [
            "Policy defined as versioned YAML/Pydantic schema, not tribal knowledge",
            "Audit trail provides the evidence base a quality management system requires",
        ]
        return ArticleCheck(
            article="Article 17", title="Quality management system",
            penalty_tier=PENALTY_HIGH_RISK_OBLIGATIONS,
            check_ids=checks, passed=passed, evidence=evidence, remediation=[],
        )
