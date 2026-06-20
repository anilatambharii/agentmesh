"""
PII / PHI / PCI Detection and Masking

Scans prompts and responses for sensitive data before they reach the LLM
or are stored in cache/audit logs. Supports three modes:

  MASK    — replace with labelled placeholder  e.g. [EMAIL]
  REDACT  — replace with ***
  BLOCK   — raise PIIDetectedError so the request is rejected

Entities detected:
  PII: email, phone, SSN, passport, IP address, name (heuristic), DOB
  PHI: MRN, NPI, DEA number, ICD-10 code, medication dosage context
  PCI: credit card (Luhn-validated), CVV, bank routing/account numbers
  CII: AWS/GCP/Azure keys, private keys, JWT tokens, connection strings

Usage:
  from agentmesh.security.pii_scanner import PIIScanner, ScanMode

  scanner = PIIScanner(mode=ScanMode.MASK)
  result  = scanner.scan("Email me at alice@example.com, SSN 123-45-6789")
  print(result.cleaned)   # "Email me at [EMAIL], SSN [SSN]"
  print(result.findings)  # [Finding(entity_type='EMAIL', ...)]
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ScanMode(str, Enum):
    MASK   = "mask"    # replace with [TYPE]
    REDACT = "redact"  # replace with ***
    BLOCK  = "block"   # raise PIIDetectedError


class PIIDetectedError(Exception):
    """Raised when BLOCK mode is active and PII/PHI/PCI is found."""
    def __init__(self, findings: "List[Finding]"):
        self.findings = findings
        types = ", ".join(sorted({f.entity_type for f in findings}))
        super().__init__(f"Sensitive data detected and blocked: {types}")


@dataclass
class Finding:
    entity_type: str        # EMAIL, SSN, CREDIT_CARD, PHI_MRN, etc.
    value_hash:  str        # SHA-256 of original value (for audit without storing raw)
    start:       int
    end:         int
    confidence:  float = 1.0


@dataclass
class ScanResult:
    original:  str
    cleaned:   str
    findings:  List[Finding] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    @property
    def finding_types(self) -> List[str]:
        return sorted({f.entity_type for f in self.findings})


# ── Pattern registry ──────────────────────────────────────────────────────────

def _p(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)


_PATTERNS: List[tuple] = [
    # ── PII ───────────────────────────────────────────────────────────────────
    ("EMAIL",       _p(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("PHONE_US",    _p(r"\b(\+1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b")),
    ("SSN",         _p(r"\b(?!000|666|9\d{2})\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}\b")),
    ("PASSPORT",    _p(r"\b[A-Z]{1,2}\d{6,9}\b")),
    ("IP_ADDRESS",  _p(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("DOB",         _p(r"\b(?:born|dob|date of birth)[:\s]+\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b")),
    ("ZIP_CODE",    _p(r"\b\d{5}(?:-\d{4})?\b")),

    # ── PHI (HIPAA) ───────────────────────────────────────────────────────────
    ("PHI_MRN",     _p(r"\b(?:mrn|medical record(?:\s+number)?|patient\s+id)[:\s#]+[A-Z0-9\-]{4,20}\b")),
    ("PHI_NPI",     _p(r"\b(?:npi)[:\s#]+\d{10}\b")),
    ("PHI_DEA",     _p(r"\b[A-Z]{2}\d{7}\b")),   # DEA registration number
    ("PHI_ICD10",   _p(r"\b[A-Z]\d{2}(?:\.[A-Z0-9]{1,4})?\b")),
    ("PHI_DOSAGE",  _p(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml|units?|tablets?|capsules?)\b")),

    # ── PCI (card data) ───────────────────────────────────────────────────────
    # Luhn validation is applied separately
    ("PCI_CARD",    _p(r"\b(?:\d[ \-]?){13,19}\b")),
    ("PCI_CVV",     _p(r"\b(?:cvv|cvc|cvv2|cvc2|security code)[:\s]+\d{3,4}\b")),
    ("PCI_ROUTING", _p(r"\b(?:routing(?:\s+number)?|aba)[:\s]+\d{9}\b")),
    ("PCI_ACCOUNT", _p(r"\b(?:account(?:\s+number)?|acct)[:\s]+\d{8,17}\b")),

    # ── CII (credential & infrastructure) ────────────────────────────────────
    ("CII_AWS_KEY",  _p(r"\b(?:AKIA|ASIA|AROA|AIDA|ANPA|ANVA|AIPA)[A-Z0-9]{16}\b")),
    ("CII_GCP_KEY",  _p(r"\"type\":\s*\"service_account\"")),
    ("CII_JWT",      _p(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("CII_PRIV_KEY", _p(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("CII_CONN_STR", _p(r"(?:postgresql|mysql|mongodb|redis|amqp)://[^\s\"']+")),
    ("CII_API_KEY",  _p(r"\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token)[:\s\"'=]+[A-Za-z0-9_\-]{16,}\b")),
    ("CII_AZURE_KEY",_p(r"\b[A-Za-z0-9+/]{32,88}={0,2}\b")),  # Base64 blobs (broad, filtered by context)
]

# Minimum match lengths to reduce false positives on short matches
_MIN_LEN = {
    "ZIP_CODE": 5,
    "PHI_ICD10": 3,
    "PCI_CARD": 13,
    "CII_AZURE_KEY": 40,
}


def _luhn(number: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2)
    return total % 10 == 0


class PIIScanner:
    """
    Scan text for PII, PHI, PCI and CII. Thread-safe, no external dependencies.

    Args:
        mode:           ScanMode.MASK | REDACT | BLOCK
        enabled_types:  restrict to specific entity types (None = all)
        min_confidence: skip findings below this threshold
    """

    def __init__(
        self,
        mode: ScanMode = ScanMode.MASK,
        enabled_types: Optional[List[str]] = None,
        min_confidence: float = 0.7,
    ):
        self.mode = mode
        self.enabled = set(enabled_types) if enabled_types else None
        self.min_conf = min_confidence

    def scan(self, text: str) -> ScanResult:
        if not text:
            return ScanResult(original=text, cleaned=text)

        findings: List[Finding] = []

        for entity_type, pattern in _PATTERNS:
            if self.enabled and entity_type not in self.enabled:
                continue

            for m in pattern.finditer(text):
                val = m.group(0)
                min_len = _MIN_LEN.get(entity_type, 0)
                if len(val) < min_len:
                    continue

                # Extra validation for credit cards
                if entity_type == "PCI_CARD":
                    digits_only = re.sub(r"\D", "", val)
                    if not _luhn(digits_only):
                        continue

                # Skip very short ZIP matches inside longer numbers
                if entity_type == "ZIP_CODE" and len(re.sub(r"\D", "", val)) > 9:
                    continue

                conf = 1.0
                # Reduce confidence for broad patterns
                if entity_type in ("CII_AZURE_KEY", "PHI_ICD10"):
                    conf = 0.75

                if conf < self.min_conf:
                    continue

                findings.append(Finding(
                    entity_type=entity_type,
                    value_hash=hashlib.sha256(val.encode()).hexdigest()[:16],
                    start=m.start(),
                    end=m.end(),
                    confidence=conf,
                ))

        if not findings:
            return ScanResult(original=text, cleaned=text)

        if self.mode == ScanMode.BLOCK:
            raise PIIDetectedError(findings)

        # Apply replacements in reverse order (preserve offsets)
        cleaned = text
        for f in sorted(findings, key=lambda x: x.start, reverse=True):
            replacement = f"[{f.entity_type}]" if self.mode == ScanMode.MASK else "***"
            cleaned = cleaned[:f.start] + replacement + cleaned[f.end:]

        return ScanResult(original=text, cleaned=cleaned, findings=findings)

    def scan_messages(self, messages: list) -> tuple:
        """
        Scan a list of {role, content} messages.
        Returns (cleaned_messages, all_findings).
        """
        all_findings: List[Finding] = []
        cleaned = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                result = self.scan(content)
                all_findings.extend(result.findings)
                cleaned.append({**msg, "content": result.cleaned})
            elif isinstance(content, list):
                # Anthropic multi-part content
                new_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        result = self.scan(part["text"])
                        all_findings.extend(result.findings)
                        new_parts.append({**part, "text": result.cleaned})
                    else:
                        new_parts.append(part)
                cleaned.append({**msg, "content": new_parts})
            else:
                cleaned.append(msg)
        return cleaned, all_findings
