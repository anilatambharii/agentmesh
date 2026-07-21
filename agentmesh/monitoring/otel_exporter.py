"""
OpenTelemetry Real-Time Exporter

Streams every AgentMesh governance event (LLM calls, cache hits, quota
blocks, injection detections, anomalies) to any OTel collector — Datadog,
Honeycomb, Grafana Tempo, Splunk, Elastic, Azure Monitor — as it happens.

Unlike AuditTrail.export_otel() (a one-shot batch export of stored audit
entries), this subscribes to the live GovernanceEvent bus and exports
continuously for the life of the process — the shape a platform team
actually wants wired into their existing observability stack.

Usage:
    from agentmesh.monitoring.otel_exporter import OTelExporter

    exporter = OTelExporter(endpoint="http://localhost:4317")
    exporter.start()   # subscribes to the bus, exports on a daemon thread
    ...
    exporter.stop()
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Dict, Optional

from agentmesh.events.bus import GovernanceEvent, get_bus

logger = logging.getLogger(__name__)

# Event kinds that increment the "blocked" counter — the metric a security
# team dashboards first, since it's the direct proxy for prevented incidents.
_BLOCK_KINDS = {"quota_block", "injection_detected", "pii_blocked", "toxicity_detected"}


class OTelExporter:
    """
    Exports live GovernanceEvents as OTLP spans + metrics.

    Degrades gracefully: if the `opentelemetry` extras aren't installed,
    start() logs a warning and the exporter becomes a no-op rather than
    raising — governance must never fail because observability tooling
    is missing.

    Args:
        endpoint:     OTLP collector endpoint, e.g. "http://localhost:4317"
        service_name: Reported as the `service.name` resource attribute
        insecure:     Skip TLS for the OTLP gRPC channel (local collectors)
        timeout:      Max seconds per export attempt, including the gRPC
                      exporter's own retry-with-backoff. Bounds how long an
                      unreachable collector can stall shutdown/export — the
                      OTel SDK default (~64s of exponential backoff) is far
                      too slow for a governance proxy's hot path.
    """

    def __init__(
        self,
        endpoint: str,
        service_name: str = "agentmesh-proxy",
        insecure: bool = True,
        timeout: float = 5.0,
    ):
        self.endpoint = endpoint
        self.service_name = service_name
        self.insecure = insecure
        self.timeout = timeout

        self._tracer: Optional[Any] = None
        self._counters: Dict[str, Any] = {}
        self._trace_provider: Optional[Any] = None
        self._meter_provider: Optional[Any] = None
        self._queue: Optional[queue.Queue] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.available = False

    def start(self) -> "OTelExporter":
        """Initialize the OTel pipeline and begin draining the event bus."""
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            logger.warning(
                "opentelemetry not installed — OTel export disabled. "
                "Run: pip install agentmesh-proxy[otel]"
            )
            return self

        resource = Resource.create({"service.name": self.service_name})

        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=self.endpoint, insecure=self.insecure, timeout=self.timeout)
            )
        )
        self._trace_provider = trace_provider
        self._tracer = trace_provider.get_tracer("agentmesh")

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=self.endpoint, insecure=self.insecure, timeout=self.timeout),
            export_timeout_millis=self.timeout * 1000,
        )
        self._meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        meter = self._meter_provider.get_meter("agentmesh")

        self._counters = {
            "llm_calls":  meter.create_counter("agentmesh.llm_calls", description="Total LLM calls governed"),
            "tokens":     meter.create_counter("agentmesh.tokens_used", description="Total tokens processed"),
            "cost_usd":   meter.create_counter("agentmesh.cost_usd", description="Total spend in USD"),
            "cache_hits": meter.create_counter("agentmesh.cache_hits", description="Semantic cache hits"),
            "blocked":    meter.create_counter("agentmesh.blocked_requests", description="Requests blocked by governance"),
        }

        self.available = True
        self._queue = get_bus().subscribe(maxsize=5000)
        self._stop.clear()
        self._thread = threading.Thread(target=self._drain, name="agentmesh-otel-exporter", daemon=True)
        self._thread.start()
        logger.info("OTel exporter started -> %s", self.endpoint)
        return self

    def stop(self) -> None:
        """
        Stop draining the bus, unsubscribe, and shut down the OTLP pipeline.

        Safe to call even if start() was never called or failed (no-op).
        Calling provider.shutdown() is what actually cancels any in-flight
        export retries — without it, background gRPC retry/backoff threads
        keep running past process shutdown, which is what a bare thread-join
        here would not fix.
        """
        self._stop.set()
        if self._queue is not None:
            get_bus().unsubscribe(self._queue)
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._trace_provider is not None:
            try:
                self._trace_provider.shutdown()
            except Exception as e:
                logger.debug("OTel trace provider shutdown failed: %s", e)
        if self._meter_provider is not None:
            try:
                self._meter_provider.shutdown()
            except Exception as e:
                logger.debug("OTel meter provider shutdown failed: %s", e)

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._export(event)
            except Exception as e:
                logger.debug("OTel export failed for event %s: %s", event.kind, e)

    def _export(self, event: GovernanceEvent) -> None:
        attrs = {
            "agentmesh.team": event.team,
            "agentmesh.user": event.user,
            "agentmesh.tool": event.tool,
            "agentmesh.model": event.model,
            "agentmesh.vendor": event.vendor,
            "agentmesh.cache_layer": event.cache_layer,
        }
        with self._tracer.start_as_current_span(f"agentmesh.{event.kind}") as span:
            for k, v in attrs.items():
                if v:
                    span.set_attribute(k, v)
            span.set_attribute("agentmesh.tokens_used", event.tokens_used)
            span.set_attribute("agentmesh.cost_usd", event.cost_usd)
            if event.message:
                span.set_attribute("agentmesh.message", event.message)

        labels = {k: v for k, v in attrs.items() if v}

        if event.kind == "llm_call":
            self._counters["llm_calls"].add(1, labels)
            if event.tokens_used:
                self._counters["tokens"].add(event.tokens_used, labels)
            if event.cost_usd:
                self._counters["cost_usd"].add(event.cost_usd, labels)
        elif event.kind == "cache_hit":
            self._counters["cache_hits"].add(1, labels)
        if event.kind in _BLOCK_KINDS:
            self._counters["blocked"].add(1, {**labels, "reason": event.kind})
