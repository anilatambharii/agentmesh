"""
AgentMesh Real-Time Observability Server

Single FastAPI app that provides:
  GET /stream              — Server-Sent Events; streams every governance event live
  GET /api/stats           — Aggregate stats from the event bus
  GET /api/quota           — Per-team/user/tool quota snapshot
  GET /api/escalations     — Escalation queue
  GET /api/vendors         — Multi-vendor cost comparison table
  GET /api/events/recent   — Last N raw events as JSON
  GET /health              — Liveness probe

Designed to run alongside any AgentMesh deployment.  Ships as a background thread
so nothing needs to change in existing agent code:

    from agentmesh.server import start_server
    mesh = AgentMesh(policy=...)
    start_server(mesh=mesh, port=7861)     # starts in daemon thread, returns immediately

Or standalone (no mesh required — derives stats from the global event bus):

    start_server(port=7861)

SSE client (JavaScript):
    const es = new EventSource('http://localhost:7861/stream?last_n=50');
    es.onmessage = e => console.log(JSON.parse(e.data));
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, AsyncGenerator, Dict, Optional

try:
    from fastapi import FastAPI, Request, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse, JSONResponse
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from agentmesh.events.bus import get_bus, GovernanceEvent


def _build_app(mesh: Optional[Any] = None) -> "FastAPI":
    if not HAS_FASTAPI:
        raise ImportError(
            "fastapi and uvicorn are required for the observability server.\n"
            "  pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="AgentMesh Observability API",
        description=(
            "Real-time governance event stream + REST snapshots for enterprise AI deployments. "
            "Every LLM call, cache decision, quota check, vendor route, and escalation "
            "is emitted here the instant it happens."
        ),
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],    # tighten in production via env var
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    bus = get_bus()

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["ops"])
    async def health():
        return {
            "status":          "ok",
            "events_in_history": len(bus.history()),
            "subscribers":     bus.subscriber_count,
            "uptime_s":        round(time.time() - _START_TIME, 1),
        }

    # ── SSE stream ────────────────────────────────────────────────────────────

    @app.get("/stream", tags=["stream"])
    async def stream(
        request: Request,
        last_n: int = Query(0, description="Replay last N events before going live"),
    ):
        """
        Server-Sent Events endpoint.  Connect with EventSource in the browser:

            const es = new EventSource('/stream?last_n=50');
            es.onmessage = e => { const ev = JSON.parse(e.data); ... };

        Each event is a JSON object with fields: kind, timestamp_iso, team, tool,
        model, vendor, tokens_used, tokens_saved, cost_usd, quota_pct, message, ...
        """

        async def generator() -> AsyncGenerator[str, None]:
            sub_q = bus.subscribe()
            try:
                # Replay recent history so new tabs don't start blank
                if last_n > 0:
                    for event in bus.recent(last_n):
                        if await request.is_disconnected():
                            return
                        yield event.to_sse()

                # Stream live events indefinitely
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = sub_q.get(timeout=0.5)
                        yield event.to_sse()
                    except queue.Empty:
                        # SSE keepalive — prevents proxies from timing out the connection
                        yield ": keepalive\n\n"
            finally:
                bus.unsubscribe(sub_q)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control":   "no-cache, no-store",
                "Connection":      "keep-alive",
                "X-Accel-Buffering": "no",   # disable nginx buffering
            },
        )

    # ── REST snapshots ────────────────────────────────────────────────────────

    @app.get("/api/stats", tags=["api"])
    async def stats():
        """Aggregate stats from the last 500 events in the bus."""
        base = bus.stats_snapshot()
        if mesh is not None:
            try:
                base["mesh"] = mesh.stats
            except Exception:
                pass
        return base

    @app.get("/api/quota", tags=["api"])
    async def quota():
        """Current token quota snapshot across all teams / users / tools."""
        if mesh is None or not getattr(mesh, "quota_enforcer", None):
            warns = [e for e in bus.recent(500) if e.kind in ("quota_warn", "quota_block")]
            return {"note": "No mesh quota enforcer attached; event-derived counts only",
                    "quota_events": len(warns)}
        try:
            summary = mesh.quota_enforcer.usage_summary()
            return {"snapshot": summary.to_dict() if hasattr(summary, "to_dict") else summary}
        except Exception as exc:
            return {"error": str(exc)}

    @app.get("/api/escalations", tags=["api"])
    async def escalations():
        """All escalation requests (pending, approved, rejected)."""
        if mesh is None or not getattr(mesh, "escalation_mgr", None):
            count = sum(1 for e in bus.recent(500) if e.kind == "escalation")
            return {"total": count, "pending": count, "requests": []}
        return mesh.escalation_mgr.summary()

    @app.get("/api/vendors", tags=["api"])
    async def vendors(
        input_tokens:  int = Query(1000, description="Estimated input tokens"),
        output_tokens: int = Query(300,  description="Estimated output tokens"),
    ):
        """Cost comparison across all configured vendors at the given token counts."""
        if mesh is not None and getattr(mesh, "multi_vendor", None):
            router = mesh.multi_vendor
        else:
            from agentmesh.optimizer.multi_vendor import MultiVendorRouter
            router = MultiVendorRouter(vendors=["anthropic", "openai", "google"])
        return {
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "comparison":    router.cost_comparison(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        }

    @app.get("/api/events/recent", tags=["api"])
    async def recent_events(n: int = Query(100, le=2000, description="Number of events")):
        """Last N raw governance events as JSON."""
        events = bus.recent(n)
        return {
            "count":  len(events),
            "total_in_history": len(bus.history()),
            "events": [e.to_dict() for e in events],
        }

    return app


_START_TIME = time.time()
_server_thread: Optional[threading.Thread] = None
_server_app:    Optional[Any]              = None


def start_server(
    mesh:      Optional[Any] = None,
    host:      str           = "0.0.0.0",
    port:      int           = 7861,
    log_level: str           = "warning",
) -> threading.Thread:
    """
    Start the AgentMesh observability server in a background daemon thread.

    Args:
        mesh:      AgentMesh instance (optional — used for richer /api/quota,
                   /api/escalations, /api/vendors responses).
        host:      Bind address (default 0.0.0.0).
        port:      Listen port (default 7861).
        log_level: Uvicorn log level.

    Returns:
        The started daemon Thread.

    Example:
        mesh = AgentMesh(policy=policy)
        start_server(mesh=mesh, port=7861)
        # SSE stream now live at http://localhost:7861/stream
    """
    global _server_thread, _server_app

    _server_app = _build_app(mesh=mesh)

    def _run() -> None:
        uvicorn.run(_server_app, host=host, port=port, log_level=log_level)

    _server_thread = threading.Thread(target=_run, name="agentmesh-obs-server", daemon=True)
    _server_thread.start()
    return _server_thread


def get_app(mesh: Optional[Any] = None) -> "FastAPI":
    """
    Return the FastAPI app without starting it.
    Useful when you want to mount it inside an existing ASGI app.

    Example:
        import uvicorn
        app = get_app(mesh=mesh)
        uvicorn.run(app, port=7861)
    """
    return _build_app(mesh=mesh)
