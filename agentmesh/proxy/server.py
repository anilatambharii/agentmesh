"""
AgentMesh HTTP proxy server — drop-in LLM governance proxy.

Run as a local proxy that intercepts any HTTP-based LLM call:
    agentmesh proxy --port 8080 --policy policy.yaml

Then point your agents at http://localhost:8080 instead of
api.anthropic.com or api.openai.com — zero code changes.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

from agentmesh.core import AgentMesh
from agentmesh.policy.engine import Policy

logger = logging.getLogger(__name__)

PROVIDER_TARGETS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
}


class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler that applies AgentMesh governance before forwarding."""

    mesh: AgentMesh = None  # set at server creation
    upstream: str = "https://api.anthropic.com"

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

        # Pre-call governance
        try:
            self.mesh.circuit_breaker.check()
            self.mesh.budget.check_pre_call(payload)
            if self.mesh.compressor:
                payload = self.mesh.compressor.maybe_compress(
                    payload, self.mesh.budget.remaining_ratio()
                )
            if self.mesh.router:
                payload = self.mesh.router.route(payload)
            self.mesh.audit.record_call(payload)
        except Exception as e:
            self._send_error(429, str(e))
            return

        # Forward to upstream
        body = json.dumps(payload).encode()
        upstream_url = f"{self.upstream}{self.path}"
        req = urllib.request.Request(
            upstream_url,
            data=body,
            headers={k: v for k, v in self.headers.items() if k.lower() != "content-length"},
            method="POST",
        )
        req.add_header("Content-Length", str(len(body)))

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                response_body = resp.read()
                self.send_response(resp.status)
                for header, value in resp.headers.items():
                    self.send_header(header, value)
                self.end_headers()
                self.wfile.write(response_body)

                # Post-call governance
                try:
                    result = json.loads(response_body)
                    self.mesh.budget.record_usage(result)
                    self.mesh.audit.record_result(result)
                    self.mesh.circuit_breaker.increment()
                except Exception:
                    pass
        except Exception as e:
            self._send_error(502, f"Upstream error: {e}")

    def _send_error(self, code: int, message: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode())

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)


class AgentMeshProxy:
    """
    Drop-in HTTP proxy that enforces AgentMesh governance on all LLM calls.

    Usage:
        proxy = AgentMeshProxy(policy=Policy.from_yaml("policy.yaml"), port=8080)
        proxy.start()  # background thread
        # Point your LLM client at http://localhost:8080
    """

    def __init__(
        self,
        policy: Optional[Policy] = None,
        port: int = 8080,
        upstream: str = "https://api.anthropic.com",
    ):
        self.port = port
        self.mesh = AgentMesh(policy=policy)
        self._upstream = upstream
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = False) -> None:
        ProxyHandler.mesh = self.mesh
        ProxyHandler.upstream = self._upstream
        self._server = HTTPServer(("", self.port), ProxyHandler)
        logger.info("AgentMesh proxy listening on port %d → %s", self.port, self._upstream)

        if blocking:
            self._server.serve_forever()
        else:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            logger.info("AgentMesh proxy stopped")

    @property
    def stats(self) -> Dict[str, Any]:
        return self.mesh.stats
