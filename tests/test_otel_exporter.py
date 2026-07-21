"""Tests for the real-time OpenTelemetry exporter."""

from __future__ import annotations

import time

import pytest

from agentmesh.events.bus import GovernanceEvent, get_bus, reset_bus
from agentmesh.monitoring.otel_exporter import OTelExporter


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_bus()
    yield
    reset_bus()


def test_exporter_noop_without_endpoint_dependencies_missing(monkeypatch):
    """If the otel SDK truly isn't importable, start() must not raise."""
    exporter = OTelExporter(endpoint="http://localhost:4317", timeout=1.0)

    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name.startswith("opentelemetry"):
            raise ImportError("simulated missing opentelemetry")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    exporter.start()
    assert exporter.available is False


def test_exporter_starts_and_drains_events(monkeypatch):
    # The real OTLPExporterMixin._export() retries unreachable collectors with
    # exponential backoff (up to ~63s) even with a short constructor `timeout` —
    # that param only bounds a single gRPC call's deadline, not the retry loop.
    # No collector is running in tests, so stub the shared base method to avoid
    # burning a minute per test on network calls that were never the point here.
    from opentelemetry.exporter.otlp.proto.grpc.exporter import OTLPExporterMixin
    from opentelemetry.sdk.trace.export import SpanExportResult
    monkeypatch.setattr(OTLPExporterMixin, "_export", lambda self, data: SpanExportResult.SUCCESS)

    exporter = OTelExporter(endpoint="http://localhost:4317", timeout=1.0).start()
    assert exporter.available is True

    bus = get_bus()
    bus.emit(GovernanceEvent(kind="llm_call", team="engineering", model="claude-haiku-4-5",
                              tokens_used=120, cost_usd=0.002))
    bus.emit(GovernanceEvent(kind="quota_block", team="engineering"))

    # Drain thread runs async — give it a moment to process the queue.
    deadline = time.time() + 2
    while exporter._queue.qsize() > 0 and time.time() < deadline:
        time.sleep(0.05)

    exporter.stop()
    assert exporter._queue.qsize() == 0


def test_exporter_stop_before_start_is_safe():
    OTelExporter(endpoint="http://localhost:4317", timeout=1.0).stop()
