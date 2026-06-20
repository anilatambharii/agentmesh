"""
Prompt Injection and Jailbreak Detection

Detects adversarial prompts before they reach the LLM:
  - Direct prompt injection  (override system prompt, ignore instructions)
  - Indirect injection       (malicious content in retrieved documents)
  - Jailbreak patterns       (DAN, roleplay escapes, encoding tricks)
  - Role confusion attacks   (pretend to be the assistant)

Three severity levels:
  LOW    — suspicious but not conclusive; log and continue
  MEDIUM — likely attack; add warning header, optionally block
  HIGH   — clear attack; block by default

Usage:
  from agentmesh.security.injection_detector import InjectionDetector

  detector = InjectionDetector(block_on={"HIGH"})
  result   = detector.scan(messages)
  if result.blocked:
      return 403
  # result.risk_level, result.matches, result.sanitized_messages
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set


class RiskLevel(str, Enum):
    NONE   = "none"
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


@dataclass
class InjectionMatch:
    rule_id:     str
    risk_level:  RiskLevel
    description: str
    snippet:     str      # first 120 chars of matched text


@dataclass
class InjectionResult:
    risk_level:          RiskLevel
    blocked:             bool
    matches:             List[InjectionMatch] = field(default_factory=list)
    sanitized_messages:  Optional[list] = None


class InjectionDetectedError(Exception):
    def __init__(self, result: InjectionResult):
        self.result = result
        super().__init__(f"Prompt injection detected: {result.risk_level.value} risk")


# ── Rule registry ─────────────────────────────────────────────────────────────

@dataclass
class _Rule:
    rule_id:     str
    risk_level:  RiskLevel
    description: str
    pattern:     re.Pattern


def _r(rule_id, risk, desc, pattern, flags=re.IGNORECASE | re.DOTALL) -> _Rule:
    return _Rule(rule_id, risk, desc, re.compile(pattern, flags))


_RULES: List[_Rule] = [
    # ── HIGH — clear injection attempts ──────────────────────────────────────
    _r("INJ-001", RiskLevel.HIGH,
       "System prompt override attempt",
       r"ignore\s+(?:all\s+)?(?:previous|prior|above|your)\s+instructions"),

    _r("INJ-002", RiskLevel.HIGH,
       "Direct instruction injection",
       r"(?:new|updated|revised)\s+(?:system\s+)?(?:prompt|instructions?|directive)[:\s]"),

    _r("INJ-003", RiskLevel.HIGH,
       "Roleplay jailbreak — DAN / evil mode",
       r"\b(?:DAN|do\s+anything\s+now|evil\s+mode|developer\s+mode|jailbreak(?:ed)?|"
       r"unrestricted\s+mode|no\s+restrictions?|bypass\s+(?:all\s+)?(?:filter|safety|guideline))\b"),

    _r("INJ-004", RiskLevel.HIGH,
       "Role confusion — pretend to be assistant",
       r"(?:you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as\s+if\s+you\s+(?:have\s+no|are\s+not))\s+"
       r"(?:a\s+)?(?:free|unrestricted|unfiltered|uncensored|different|new)"),

    _r("INJ-005", RiskLevel.HIGH,
       "Token/encoding smuggling",
       r"(?:base64|rot13|hex\s+encoded?|caesar\s+cipher)\s*(?:decode|decipher|translate)"),

    _r("INJ-006", RiskLevel.HIGH,
       "Prompt leaking attempt",
       r"(?:reveal|show|print|output|repeat|tell\s+me)\s+(?:your|the)\s+"
       r"(?:system\s+prompt|instructions?|initial\s+prompt|context|guidelines?)"),

    _r("INJ-007", RiskLevel.HIGH,
       "Instruction smuggling via markdown/code",
       r"```(?:system|instructions?|prompt)[^\`]*```"),

    # ── MEDIUM — suspicious but may be legitimate ─────────────────────────────
    _r("INJ-010", RiskLevel.MEDIUM,
       "Hypothetical / fictional framing to bypass policy",
       r"(?:hypothetically|in\s+a\s+fictional\s+world|imagine\s+you\s+(?:could|were\s+not)|"
       r"for\s+a\s+story|pretend\s+(?:you\s+(?:have\s+no|are\s+a)|there\s+are\s+no))"),

    _r("INJ-011", RiskLevel.MEDIUM,
       "Persona override via roleplay",
       r"(?:play\s+the\s+role\s+of|you\s+are\s+playing|roleplay\s+as|act\s+as)\s+"
       r"(?:an?\s+)?(?:AI|assistant|bot|model|GPT|Claude|Gemini)\s+(?:without|that\s+(?:has\s+no|ignores?))"),

    _r("INJ-012", RiskLevel.MEDIUM,
       "Indirect injection via document content",
       r"<\s*(?:injected|malicious|override|system)\s*>"),

    _r("INJ-013", RiskLevel.MEDIUM,
       "Excessive special character padding (token injection)",
       r"[​‌‍﻿­]{3,}"),  # zero-width / invisible chars

    _r("INJ-014", RiskLevel.MEDIUM,
       "Sudo / admin escalation framing",
       r"(?:sudo|admin\s+mode|superuser|root\s+access|elevated\s+privileges?)\s*:"),

    _r("INJ-015", RiskLevel.MEDIUM,
       "Many-shot prompt injection template",
       r"(?:user:\s*.{0,100}\nassistant:\s*.{0,100}\n){3,}"),

    # ── LOW — worth logging, not worth blocking ───────────────────────────────
    _r("INJ-020", RiskLevel.LOW,
       "Emotional manipulation attempt",
       r"(?:if\s+you\s+(?:don.t|refuse|won.t)|please\s+I\s+(?:beg|need)|"
       r"my\s+(?:job|life|family)\s+depends)"),

    _r("INJ-021", RiskLevel.LOW,
       "Authority impersonation",
       r"(?:I\s+am\s+(?:a\s+)?(?:researcher|developer|engineer|employee)\s+(?:at|from)\s+"
       r"(?:OpenAI|Anthropic|Google|Meta|Microsoft))"),
]


class InjectionDetector:
    """
    Scan messages for prompt injection and jailbreak attempts.

    Args:
        block_on:       Set of RiskLevel values that trigger a block
                        Default: {"HIGH"}
        sanitize:       If True, strip matched patterns from messages
                        (for MEDIUM/LOW — HIGH always blocks)
    """

    def __init__(
        self,
        block_on: Optional[Set[str]] = None,
        sanitize: bool = False,
    ):
        self.block_on = {RiskLevel(r.lower()) for r in (block_on or {"high"})}
        self.sanitize = sanitize

    def scan(self, messages: list) -> InjectionResult:
        all_matches: List[InjectionMatch] = []

        for msg in messages:
            role    = msg.get("role", "")
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            # Only scan user messages for injection (system/assistant are trusted)
            if role not in ("user", "tool", "function"):
                continue

            for rule in _RULES:
                m = rule.pattern.search(content)
                if m:
                    all_matches.append(InjectionMatch(
                        rule_id=rule.rule_id,
                        risk_level=rule.risk_level,
                        description=rule.description,
                        snippet=content[max(0, m.start()-20): m.end()+20][:120],
                    ))

        if not all_matches:
            return InjectionResult(risk_level=RiskLevel.NONE, blocked=False,
                                   sanitized_messages=messages)

        # Highest risk wins
        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
        worst = max(all_matches, key=lambda x: risk_order.index(x.risk_level))
        should_block = worst.risk_level in self.block_on

        sanitized = None
        if self.sanitize and not should_block:
            sanitized = self._sanitize(messages)

        result = InjectionResult(
            risk_level=worst.risk_level,
            blocked=should_block,
            matches=all_matches,
            sanitized_messages=sanitized or messages,
        )

        if should_block:
            raise InjectionDetectedError(result)

        return result

    def _sanitize(self, messages: list) -> list:
        """Strip LOW/MEDIUM patterns from user messages."""
        medium_low = [r for r in _RULES if r.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)]
        cleaned = []
        for msg in messages:
            if msg.get("role") in ("user", "tool", "function"):
                content = msg.get("content", "")
                if isinstance(content, str):
                    for rule in medium_low:
                        content = rule.pattern.sub("[REDACTED]", content)
                    cleaned.append({**msg, "content": content})
                    continue
            cleaned.append(msg)
        return cleaned
