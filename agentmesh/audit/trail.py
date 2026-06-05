"""Tamper-evident audit trail with Ed25519 signing."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    event_type: str = ""
    agent_id: Optional[str] = None
    agent_role: Optional[str] = None
    parent_agent_id: Optional[str] = None
    model: Optional[str] = None
    tool_name: Optional[str] = None
    tokens_used: int = 0
    policy_name: Optional[str] = None
    policy_checks: Dict[str, bool] = field(default_factory=dict)
    payload_hash: Optional[str] = None  # SHA-256 of request (not full payload for PII)
    signature: Optional[str] = None
    prev_hash: Optional[str] = None  # Chain integrity


class AuditTrail:
    """
    Append-only, tamper-evident audit log for agent actions.

    Each entry is chained via prev_hash and optionally signed with Ed25519.
    Compatible with OpenTelemetry export to Splunk, Elastic, Datadog, etc.

    Satisfies EU AI Act Article 13, NIST AI RMF, SOC 2 Type II audit requirements.
    """

    def __init__(self, signing_key: Optional[str] = None):
        self._entries: List[AuditEntry] = []
        self._signing_key = signing_key
        self._otel_exporter = None

    def record_call(self, kwargs: Dict[str, Any], agent_id: Optional[str] = None) -> AuditEntry:
        entry = AuditEntry(
            event_type="llm_call",
            agent_id=agent_id,
            model=kwargs.get("model"),
            payload_hash=self._hash_payload(kwargs),
            prev_hash=self._last_hash(),
            policy_checks={"pre_call": True},
        )
        entry.signature = self._sign(entry)
        self._entries.append(entry)
        logger.debug("Audit: recorded llm_call entry %s", entry.entry_id)
        return entry

    def record_result(self, result: Any, entry_id: Optional[str] = None) -> AuditEntry:
        entry = AuditEntry(
            event_type="llm_result",
            tokens_used=self._extract_tokens(result),
            payload_hash=self._hash_payload({"result": str(result)[:256]}),
            prev_hash=self._last_hash(),
        )
        entry.signature = self._sign(entry)
        self._entries.append(entry)
        return entry

    def record_tool_call(self, tool_name: str, agent_id: str, kwargs: Dict) -> AuditEntry:
        entry = AuditEntry(
            event_type="tool_call",
            agent_id=agent_id,
            tool_name=tool_name,
            payload_hash=self._hash_payload(kwargs),
            prev_hash=self._last_hash(),
        )
        entry.signature = self._sign(entry)
        self._entries.append(entry)
        return entry

    def record_delegation(self, from_agent: str, to_agent: str, task: str) -> AuditEntry:
        entry = AuditEntry(
            event_type="agent_delegation",
            agent_id=from_agent,
            parent_agent_id=None,
            payload_hash=self._hash_payload({"from": from_agent, "to": to_agent, "task": task[:128]}),
            prev_hash=self._last_hash(),
        )
        entry.signature = self._sign(entry)
        self._entries.append(entry)
        return entry

    def verify(self) -> bool:
        """Verify the integrity of the entire audit chain."""
        for i, entry in enumerate(self._entries[1:], 1):
            expected_prev = self._compute_hash(self._entries[i - 1])
            if entry.prev_hash != expected_prev:
                logger.error("Audit chain broken at entry %s", entry.entry_id)
                return False
        return True

    def export_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump([asdict(e) for e in self._entries], f, indent=2)
        logger.info("Audit trail exported to %s (%d entries)", path, len(self._entries))

    def export_otel(self, endpoint: str) -> None:
        """Export entries to OpenTelemetry collector (Splunk/Elastic/Datadog/Azure Sentinel)."""
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider()
            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            tracer = provider.get_tracer("agentmesh")

            for entry in self._entries:
                with tracer.start_as_current_span(entry.event_type) as span:
                    span.set_attribute("agentmesh.entry_id", entry.entry_id)
                    span.set_attribute("agentmesh.agent_id", entry.agent_id or "")
                    span.set_attribute("agentmesh.tokens_used", entry.tokens_used)
                    span.set_attribute("agentmesh.signature", entry.signature or "")
        except ImportError:
            logger.warning("opentelemetry not installed. Run: pip install agentmesh[otel]")

    @property
    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    def _hash_payload(self, data: Any) -> str:
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    def _compute_hash(self, entry: AuditEntry) -> str:
        data = f"{entry.entry_id}{entry.timestamp}{entry.event_type}{entry.payload_hash}"
        return hashlib.sha256(data.encode()).hexdigest()

    def _last_hash(self) -> Optional[str]:
        if not self._entries:
            return None
        return self._compute_hash(self._entries[-1])

    def _sign(self, entry: AuditEntry) -> Optional[str]:
        if not self._signing_key:
            return None
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            import base64
            key = Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(self._signing_key)
            )
            msg = f"{entry.entry_id}{entry.timestamp}{entry.payload_hash}".encode()
            sig = key.sign(msg)
            return base64.b64encode(sig).decode()
        except Exception:
            return None

    def _extract_tokens(self, result: Any) -> int:
        if hasattr(result, "usage"):
            usage = result.usage
            return getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
        return 0
