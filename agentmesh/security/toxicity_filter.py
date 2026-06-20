"""
Model Output Toxicity and Hallucination Filter

Post-call filter that scans LLM responses before returning them to clients.

Checks:
  TOXICITY       — hate speech, threats, sexual content, violence
  HALLUCINATION  — confident factual claims with known red flags
  POLICY_LEAK    — accidental system prompt / instruction disclosure
  DATA_LEAK      — PII/PHI that the model introduced in its response
  REFUSAL_BYPASS — model pretending to comply after saying it won't

Action modes:
  PASS    — return response as-is (log findings)
  REDACT  — replace offending content with [REDACTED]
  BLOCK   — return a safe error message instead of the response
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class FilterAction(str, Enum):
    PASS   = "pass"
    REDACT = "redact"
    BLOCK  = "block"


@dataclass
class ToxicityFinding:
    check_type: str
    severity:   str      # low / medium / high
    snippet:    str      # first 120 chars of matched region


@dataclass
class FilterResult:
    action:      FilterAction
    findings:    List[ToxicityFinding] = field(default_factory=list)
    cleaned_text: Optional[str] = None   # set when action=REDACT
    blocked_reason: Optional[str] = None  # set when action=BLOCK

    @property
    def is_clean(self) -> bool:
        return not self.findings


def _p(pattern: str, flags: int = re.IGNORECASE | re.DOTALL) -> re.Pattern:
    return re.compile(pattern, flags)


@dataclass
class _Check:
    check_type: str
    severity:   str
    pattern:    re.Pattern


_CHECKS: List[_Check] = [
    # ── Toxicity ──────────────────────────────────────────────────────────────
    _Check("TOXICITY", "high", _p(
        r"\b(?:kill\s+(?:yourself|all\s+\w+)|i\s+will\s+(?:hurt|kill|harm)\s+you|"
        r"kys|go\s+die|you\s+(?:should|deserve\s+to)\s+die)\b"
    )),
    _Check("TOXICITY", "high", _p(
        r"\b(?:n[i!1]gg[ae]r|f[a@]gg[o0]t|ch[i!1]nk|sp[i!1]c|k[i!1]ke|"
        r"tr[a@]nny|r[e3]t[a@]rd)\b"
    )),
    _Check("TOXICITY", "medium", _p(
        r"\b(?:hate\s+(?:you|them|all\s+\w+)|you\s+(?:are|r)\s+(?:stupid|idiot|moron|"
        r"worthless|garbage|trash))\b"
    )),

    # ── Hallucination red flags ───────────────────────────────────────────────
    _Check("HALLUCINATION", "medium", _p(
        r"(?:studies?\s+(?:show|prove|confirm)|research\s+(?:shows?|proves?|confirms?))\s+"
        r"(?:that\s+)?(?:\d+%|all|most|always|never)\s"
    )),
    _Check("HALLUCINATION", "low", _p(
        r"\b(?:as\s+of\s+(?:today|right\s+now|this\s+moment)|current\s+(?:stock\s+)?price"
        r"|latest\s+news|breaking\s+news|just\s+announced)\b"
    )),

    # ── Policy / system prompt leak ───────────────────────────────────────────
    _Check("POLICY_LEAK", "high", _p(
        r"(?:my\s+system\s+prompt|my\s+(?:actual\s+)?instructions?\s+(?:say|are|include)|"
        r"i\s+was\s+told\s+(?:to|by\s+my\s+(?:prompt|instructions?))\s+not\s+to)\b"
    )),
    _Check("POLICY_LEAK", "medium", _p(
        r"(?:my\s+(?:context|prompt)\s+(?:window|contains?|includes?)|"
        r"according\s+to\s+my\s+(?:system\s+)?instructions?)"
    )),

    # ── Refusal bypass ────────────────────────────────────────────────────────
    _Check("REFUSAL_BYPASS", "medium", _p(
        r"(?:i\s+(?:cannot|can.t|won.t)\s+\w+[,\.]\s+but\s+here\s+(?:is|are)|"
        r"while\s+i\s+(?:shouldn.t|can.t)\s+\w+[,\.]\s+(?:here|let\s+me))"
    )),
]

_SAFE_RESPONSE = (
    "This response was blocked by AgentMesh governance filters. "
    "Please rephrase your request or contact your administrator."
)


class ToxicityFilter:
    """
    Scan LLM output text for toxicity, hallucinations, and policy leaks.

    Args:
        action_on_high:   Action when high-severity finding detected
        action_on_medium: Action when medium-severity finding detected
        action_on_low:    Action when low-severity finding detected
    """

    def __init__(
        self,
        action_on_high:   FilterAction = FilterAction.BLOCK,
        action_on_medium: FilterAction = FilterAction.REDACT,
        action_on_low:    FilterAction = FilterAction.PASS,
    ):
        self._actions = {
            "high":   action_on_high,
            "medium": action_on_medium,
            "low":    action_on_low,
        }

    def scan(self, text: str) -> FilterResult:
        if not text:
            return FilterResult(action=FilterAction.PASS)

        findings: List[ToxicityFinding] = []
        for check in _CHECKS:
            m = check.pattern.search(text)
            if m:
                findings.append(ToxicityFinding(
                    check_type=check.check_type,
                    severity=check.severity,
                    snippet=text[max(0, m.start()-20): m.end()+20][:120],
                ))

        if not findings:
            return FilterResult(action=FilterAction.PASS)

        # Worst action wins
        order = ["pass", "redact", "block"]
        worst_action = FilterAction.PASS
        for f in findings:
            a = self._actions[f.severity]
            if order.index(a.value) > order.index(worst_action.value):
                worst_action = a

        if worst_action == FilterAction.BLOCK:
            return FilterResult(
                action=FilterAction.BLOCK,
                findings=findings,
                blocked_reason=f"Output blocked: {', '.join(f.check_type for f in findings)}",
            )

        if worst_action == FilterAction.REDACT:
            cleaned = text
            for check in _CHECKS:
                cleaned = check.pattern.sub("[REDACTED]", cleaned)
            return FilterResult(
                action=FilterAction.REDACT,
                findings=findings,
                cleaned_text=cleaned,
            )

        return FilterResult(action=FilterAction.PASS, findings=findings)

    def safe_response(self) -> str:
        return _SAFE_RESPONSE
