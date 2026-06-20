"""
AgentMesh OpenAI-Compatible Proxy Server

Sits transparently in front of every AI tool.  Tools send requests here instead
of calling Anthropic / OpenAI / Google directly.  AgentMesh applies the full
governance stack — cache, quota, vendor routing, compression, audit — then
forwards to the real LLM and returns the response in the exact format the tool
expects.

Wire formats supported:
  POST /v1/messages            — Anthropic (Claude Code, Claude Desktop, Anthropic SDK)
  POST /v1/chat/completions    — OpenAI (VS Code Copilot, Cursor, most tools)
  GET  /v1/models              — list models (tool capability discovery)
  GET  /health                 — liveness probe

Identity headers (optional, for per-team quota + attribution):
  X-AgentMesh-Team:   engineering
  X-AgentMesh-User:   alice@company.com
  X-AgentMesh-Tool:   vscode-copilot

Governance response headers (always returned):
  X-AgentMesh-Cache:      hit | miss
  X-AgentMesh-Vendor:     anthropic | openai | google | ...
  X-AgentMesh-Model:      claude-haiku-4-5 | ...
  X-AgentMesh-Tokens:     1234
  X-AgentMesh-Cost-USD:   0.000123
  X-AgentMesh-Quota-Pct:  72%
  X-AgentMesh-Demo:       true  (when running without real API keys)

Quick start:
    agentmesh serve --port 8080 --demo

Then in any tool:
    export ANTHROPIC_BASE_URL=http://localhost:8080      # Claude Code
    OPENAI_BASE_URL=http://localhost:8080/v1             # OpenAI SDK tools
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agentmesh.events.bus import GovernanceEvent, get_bus
from agentmesh.proxy.forwarder import forward, forward_stream, has_api_key
from agentmesh.proxy.middleware import (
    estimate_input_tokens,
    extract_response_content,
    format_response_for_client,
    last_user_message,
    normalize_anthropic_request,
    normalize_openai_request,
)

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass
class ProxyConfig:
    """All settings for the AgentMesh governance proxy."""
    vendors:               List[str]       = field(default_factory=lambda: ["anthropic"])
    routing_strategy:      str             = "cheapest_capable"
    default_model:         str             = "claude-haiku-4-5"
    team_monthly_tokens:   Dict[str, int]  = field(default_factory=dict)
    global_monthly_tokens: int             = 10_000_000
    quota_warn_pct:        float           = 0.80
    quota_hard_stop_pct:   float           = 1.00
    auto_escalate:         bool            = True
    temp_grant_tokens:     int             = 50_000
    enable_cache:          bool            = True
    enable_compression:    bool            = True
    cache_threshold:       float           = 0.70  # sentence-transformers threshold; 0.88 was char-bigram
    require_approval:      bool            = False
    demo_mode:             bool            = False
    host:                  str             = "0.0.0.0"
    port:                  int             = 8080
    log_level:             str             = "warning"
    # Deterministic mode: team -> pinned model (empty string = keep routed model, temperature=0 only)
    # Example: {"healthcare": "claude-haiku-4-5", "legal": "claude-sonnet-4-6"}
    deterministic_teams:   Dict[str, str]  = field(default_factory=dict)
    # ── Enterprise security / governance ──────────────────────────────────────
    pii_mode:              str             = ""     # "mask" | "redact" | "block" | "" (disabled)
    pii_entity_types:      List[str]       = field(default_factory=list)  # empty = all types
    block_injections:      bool            = True   # block HIGH prompt injection by default
    anomaly_detection:     bool            = True
    toxicity_filter:       bool            = True
    redis_url:             str             = ""     # empty = in-memory cache only
    slack_webhook:         str             = ""     # Slack incoming webhook URL
    pagerduty_key:         str             = ""     # PagerDuty Events API routing key
    sso_enabled:           bool            = False  # extract identity from JWT/SAML headers


# ── Identity extraction ───────────────────────────────────────────────────────

_TOOL_UA = {
    "vscode": "vscode-copilot", "copilot": "vscode-copilot",
    "cursor": "cursor", "claude-code": "claude-code",
    "anthropic": "anthropic-sdk", "openai": "openai-sdk",
    "github": "github-ci",
}


def _detect_tool(ua: str) -> str:
    ua = ua.lower()
    for pat, name in _TOOL_UA.items():
        if pat in ua:
            return name
    return "unknown"


def _extract_identity(request: Request) -> Any:
    from agentmesh.quota.engine import QuotaIdentity
    team = request.headers.get("X-AgentMesh-Team", "")
    if not team:
        logger.warning(
            "X-AgentMesh-Team header missing — request routed to shared 'default' quota pool. "
            "Set X-AgentMesh-Team on every request for proper per-team governance."
        )
    return QuotaIdentity(
        user=request.headers.get("X-AgentMesh-User", ""),
        team=team or "default",
        tool=request.headers.get("X-AgentMesh-Tool", "")
             or _detect_tool(request.headers.get("User-Agent", "")),
    )


def _client_key(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key") or request.headers.get("X-Api-Key")


# ── App builder ───────────────────────────────────────────────────────────────

def build_proxy_app(config: ProxyConfig) -> FastAPI:
    """Build and return the FastAPI proxy application."""
    app = FastAPI(
        title="AgentMesh Governance Proxy",
        description=(
            "OpenAI-compatible proxy that applies the full AgentMesh governance stack "
            "(cache · quota · vendor routing · compression · audit) to every LLM call."
        ),
        version="0.2.0",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    mesh = _build_mesh(config)
    bus  = get_bus()

    @app.get("/health")
    async def health():
        return {
            "status":    "ok",
            "demo_mode": config.demo_mode,
            "vendors":   config.vendors,
            "cache":     mesh.cache.stats if mesh.cache else None,
            "security": {
                "pii_mode":          config.pii_mode or "disabled",
                "injection_detection": config.block_injections,
                "toxicity_filter":   config.toxicity_filter,
                "anomaly_detection": config.anomaly_detection,
                "sso_enabled":       config.sso_enabled,
            },
        }

    @app.get("/v1/models")
    async def list_models():
        from agentmesh.optimizer.multi_vendor import VENDOR_CATALOG
        models = []
        for vendor in config.vendors:
            for _tier, info in VENDOR_CATALOG.get(vendor, {}).items():
                models.append({"id": info["model"], "object": "model",
                                "created": 1_700_000_000, "owned_by": vendor})
        return {"object": "list", "data": models}

    @app.post("/v1/messages")
    async def messages_ep(request: Request):
        body = await request.json()
        return await _govern(request, normalize_anthropic_request(body), mesh, bus, config)

    @app.post("/v1/chat/completions")
    async def completions_ep(request: Request):
        body = await request.json()
        return await _govern(request, normalize_openai_request(body), mesh, bus, config)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def catchall(request: Request, path: str):
        return JSONResponse(
            status_code=404,
            content={"error": f"Unknown endpoint /{path}. "
                     "Use /v1/messages or /v1/chat/completions."},
        )

    return app


# ── Core governance flow ──────────────────────────────────────────────────────

async def _govern(
    request: Request,
    internal: Dict[str, Any],
    mesh: Any,
    bus: Any,
    config: ProxyConfig,
) -> Any:
    t0         = time.monotonic()
    client_fmt = internal.get("_fmt", "openai")
    messages   = internal["messages"]
    model_hint = internal.get("model", config.default_model)
    stream     = internal.get("stream", False)
    max_tokens = internal.get("max_tokens", 1024)
    temperature = internal.get("temperature", 1.0)
    api_key    = _client_key(request)
    identity   = _extract_identity(request)
    team, user, tool = identity.team, identity.user, identity.tool

    hdrs: Dict[str, str] = {
        "X-AgentMesh-Team": team,
        "X-AgentMesh-Tool": tool,
    }
    if team == "default" and not request.headers.get("X-AgentMesh-Team"):
        hdrs["X-AgentMesh-Team-Inferred"] = "true"

    # ── 0.5. SSO / SAML identity override ────────────────────────────────────
    if config.sso_enabled and mesh.sso_extractor:
        sso_id = mesh.sso_extractor.extract(dict(request.headers))
        if sso_id:
            team = sso_id.team or team
            user = sso_id.user or user
            hdrs["X-AgentMesh-Team"]      = team
            hdrs["X-AgentMesh-SSO-Source"] = sso_id.source
            identity = identity.__class__(user=user, team=team, tool=tool)

    def _emit(kind: str, **kw):
        try:
            bus.emit(GovernanceEvent(kind=kind, team=team, user=user, tool=tool, **kw))
        except Exception:
            pass

    # ── 1. Cache check ────────────────────────────────────────────────────────
    cache_key = last_user_message(internal)
    if mesh.cache and cache_key:
        hit = mesh.cache.get(cache_key)
        if hit is not None:
            content, ti, to = extract_response_content(hit, "anthropic")
            _emit("cache_hit", model=model_hint, cache_layer="semantic",
                  tokens_saved=ti + to, message=f"saved {ti + to} tokens")
            hdrs.update({
                "X-AgentMesh-Cache":         "hit",
                "X-AgentMesh-Tokens":        "0",
                "X-AgentMesh-Cost-USD":      "0.000000",
                "X-AgentMesh-Deterministic": "true" if team in config.deterministic_teams else "false",
            })
            return JSONResponse(
                content=format_response_for_client(content, model_hint, client_fmt,
                                                   input_tokens=ti, output_tokens=to, cached=True),
                headers=hdrs,
            )
    _emit("cache_miss", model=model_hint)

    # ── 1.5. Prompt injection detection ──────────────────────────────────────
    if mesh.injection_detector:
        from agentmesh.security.injection_detector import InjectionDetectedError
        try:
            inj = mesh.injection_detector.scan(messages)
            if inj.risk_level.value != "none":
                hdrs["X-AgentMesh-Injection-Risk"] = inj.risk_level.value
                _emit("injection_detected", risk_level=inj.risk_level.value,
                      rules=[m.rule_id for m in inj.matches],
                      message=f"Prompt injection risk={inj.risk_level.value}")
                if mesh.alert_router and inj.risk_level.value == "high":
                    mesh.alert_router.alert(
                        title="Prompt Injection Detected",
                        message=f"Team '{team}' — high risk injection detected and blocked",
                        severity="critical", team=team,
                    )
        except InjectionDetectedError as e:
            hdrs["X-AgentMesh-Injection-Risk"] = "high"
            return JSONResponse(
                status_code=400, headers=hdrs,
                content={"error": {"type": "injection_detected",
                                   "message": "Request blocked: prompt injection detected",
                                   "rules": [m.rule_id for m in e.result.matches]}},
            )

    # ── 2. Quota check ────────────────────────────────────────────────────────
    if mesh.quota_enforcer:
        from agentmesh.quota.engine import QuotaStatus
        estimated = estimate_input_tokens(internal)
        qr = mesh.quota_enforcer.check(identity, estimated_tokens=estimated)
        hdrs["X-AgentMesh-Quota-Pct"] = f"{qr.pct_used:.0%}"
        if qr.status == QuotaStatus.BLOCK:
            esc_id = ""
            if mesh.escalation_mgr:
                esc = mesh.escalation_mgr.request(
                    identity=identity, quota_result=qr,
                    reason=f"Quota exceeded via {tool} ({client_fmt} proxy)",
                )
                esc_id = esc.id
            _emit("quota_block", quota_pct=qr.pct_used,
                  quota_used=qr.used_tokens, quota_limit=qr.limit_tokens,
                  escalation_id=esc_id, message=qr.message)
            return JSONResponse(
                status_code=429, headers=hdrs,
                content={"error": {"type": "quota_exceeded", "message": qr.message,
                                   "escalation_id": esc_id}},
            )
        if qr.status == QuotaStatus.WARN:
            _emit("quota_warn", quota_pct=qr.pct_used,
                  quota_used=qr.used_tokens, quota_limit=qr.limit_tokens,
                  message=qr.message)
            hdrs["X-AgentMesh-Quota-Warn"] = qr.message

    # ── 2.5. PII / PHI / PCI scanning ────────────────────────────────────────
    if mesh.pii_scanner and config.pii_mode:
        from agentmesh.security.pii_scanner import PIIDetectedError
        try:
            messages, pii_findings = mesh.pii_scanner.scan_messages(messages)
            internal = {**internal, "messages": messages}
            if pii_findings:
                types = sorted({f.entity_type for f in pii_findings})
                hdrs["X-AgentMesh-PII-Findings"] = str(len(pii_findings))
                hdrs["X-AgentMesh-PII-Types"]    = ",".join(types)
                _emit("pii_detected", count=len(pii_findings), types=types,
                      mode=config.pii_mode,
                      message=f"PII {config.pii_mode}: {len(pii_findings)} entities ({', '.join(types)})")
        except PIIDetectedError as e:
            hdrs["X-AgentMesh-PII-Findings"] = str(len(e.findings))
            return JSONResponse(
                status_code=400, headers=hdrs,
                content={"error": {"type": "pii_blocked",
                                   "message": "Request blocked: sensitive data detected",
                                   "types": sorted({f.entity_type for f in e.findings})}},
            )

    # ── 3. Prompt compression ─────────────────────────────────────────────────
    if mesh.compressor and mesh.budget.remaining_ratio() < 0.30:
        pre_len  = len(messages)
        internal = mesh.compressor.maybe_compress(internal, mesh.budget.remaining_ratio())
        messages = internal["messages"]
        if len(messages) < pre_len:
            _emit("compress", model=model_hint,
                  message=f"Compressed {pre_len}→{len(messages)} messages")
    hdrs["X-AgentMesh-Compressed"] = str(len(messages) < len(internal.get("messages", messages))).lower()

    # ── 4. Dry-run / prompt preview ───────────────────────────────────────────
    if config.require_approval or request.headers.get("X-AgentMesh-Dry-Run") == "true":
        preview = (
            f"[AgentMesh Preview — send with X-AgentMesh-Dry-Run: false to execute]\n\n"
            + "\n".join(f"[{m['role']}] {str(m.get('content',''))[:300]}" for m in messages[-4:])
            + f"\n\nEstimated input tokens: ~{estimate_input_tokens(internal):,}"
        )
        hdrs["X-AgentMesh-Dry-Run"] = "true"
        return JSONResponse(
            content=format_response_for_client(preview, model_hint, client_fmt),
            headers=hdrs,
        )

    # ── 5. Vendor routing ─────────────────────────────────────────────────────
    vendor = config.vendors[0] if config.vendors else "anthropic"
    model  = model_hint
    if len(config.vendors) > 1 and mesh.multi_vendor:
        dec    = mesh.multi_vendor.route(cache_key or "general request")
        vendor = dec.vendor
        model  = dec.model
        _emit("vendor_route", vendor=vendor, model=model,
              complexity_score=dec.complexity_score,
              cost_usd=dec.estimated_cost,
              message=f"{dec.tier.value} tier | ${dec.estimated_cost:.5f}")
    hdrs.update({"X-AgentMesh-Vendor": vendor, "X-AgentMesh-Model": model})

    # ── 5.5. Deterministic mode ───────────────────────────────────────────────
    # Per-team enforcement: temperature forced to 0.0 and optionally model pinned.
    # Guarantees the semantic cache always returns the same response for a given
    # normalised prompt — required for healthcare, legal, and compliance workloads.
    deterministic = False
    if team in config.deterministic_teams:
        deterministic = True
        temperature = 0.0
        pinned = config.deterministic_teams[team]
        if pinned:
            model = pinned
        hdrs["X-AgentMesh-Deterministic"] = "true"
        hdrs["X-AgentMesh-Model"] = model
        _emit("deterministic_enforced", model=model, temperature=0.0,
              message=f"team '{team}' deterministic mode: temperature=0, model={model}")
    else:
        hdrs["X-AgentMesh-Deterministic"] = "false"

    # ── 6. Audit ──────────────────────────────────────────────────────────────
    mesh.audit.record_call({"messages": messages, "model": model})

    use_demo = config.demo_mode or not has_api_key(vendor, api_key)
    hdrs["X-AgentMesh-Demo"] = str(use_demo).lower()

    # ── 7a. Streaming ─────────────────────────────────────────────────────────
    if stream:
        _stream_chunks: list = []

        async def streamer() -> AsyncGenerator[bytes, None]:
            async for chunk in forward_stream(
                vendor=vendor, model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                client_key=api_key, demo_mode=use_demo,
            ):
                _stream_chunks.append(chunk)
                yield chunk

            # Post-stream: quota + cache
            est = estimate_input_tokens(internal) + 50
            _emit("llm_call", model=model, vendor=vendor, tokens_used=est,
                  message=f"stream | ~{est} tokens")
            if mesh.quota_enforcer:
                mesh.quota_enforcer.consume(identity, est)

            # Extract text from accumulated SSE bytes and cache it
            if mesh.cache and cache_key:
                full_text = _extract_sse_text(_stream_chunks)
                if full_text:
                    cached_resp = {
                        "content": [{"type": "text", "text": full_text}],
                        "model": model,
                        "usage": {"input_tokens": estimate_input_tokens(internal),
                                  "output_tokens": max(10, len(full_text) // 4)},
                    }
                    mesh.cache.put(cache_key, cached_resp, model=model, tokens=est)

        return StreamingResponse(
            streamer(), media_type="text/event-stream",
            headers={**hdrs, "Cache-Control": "no-cache"},
        )

    # ── 7b. Non-streaming ─────────────────────────────────────────────────────
    raw = await forward(
        vendor=vendor, model=model, messages=messages,
        max_tokens=max_tokens, temperature=temperature,
        client_key=api_key, demo_mode=use_demo,
    )
    content, tok_in, tok_out = extract_response_content(raw, vendor)
    tokens_total = tok_in + tok_out

    # ── 8. Post-call governance ───────────────────────────────────────────────
    mesh.audit.record_result(raw)
    if tokens_total > 0:
        mesh.budget.record_usage(raw)
        if mesh.quota_enforcer:
            mesh.quota_enforcer.consume(identity, tokens_total)

    # Only cache clean, successful responses — never cache errors or zero-token responses
    if mesh.cache and cache_key and content and tokens_total > 0 and not raw.get("_error"):
        mesh.cache.put(cache_key, raw, model=model, tokens=tokens_total)

    # ── 8.5. Output toxicity filter ───────────────────────────────────────────
    if mesh.toxicity_filter and content:
        tox = mesh.toxicity_filter.scan(content)
        if tox.findings:
            types = sorted({f.check_type for f in tox.findings})
            hdrs["X-AgentMesh-Toxicity"] = ",".join(types)
            _emit("toxicity_detected", types=types, action=tox.action.value,
                  message=f"Output toxicity: {', '.join(types)} → {tox.action.value}")
        if tox.action.value == "block":
            content = mesh.toxicity_filter.safe_response()
            hdrs["X-AgentMesh-Toxicity-Action"] = "blocked"
        elif tox.action.value == "redact" and tox.cleaned_text:
            content = tox.cleaned_text
            hdrs["X-AgentMesh-Toxicity-Action"] = "redacted"

    # ── 9. Cost calculation ───────────────────────────────────────────────────
    from agentmesh.optimizer.multi_vendor import (VENDOR_CATALOG,
        _complexity_score, _tier_from_score)
    score, _ = _complexity_score(cache_key)
    tier_key  = _tier_from_score(score)
    cat       = VENDOR_CATALOG.get(vendor, {}).get(tier_key, {})
    cost_usd  = (tok_in  / 1_000_000) * cat.get("input_per_1m",  0) + \
                (tok_out / 1_000_000) * cat.get("output_per_1m", 0)

    _emit("llm_call", model=model, vendor=vendor, tokens_used=tokens_total,
          cost_usd=cost_usd,
          message=f"{tok_in}in + {tok_out}out = {tokens_total} tok | ${cost_usd:.5f}")

    # ── 9.5. Anomaly detection ────────────────────────────────────────────────
    if mesh.anomaly_detector and config.anomaly_detection:
        anomaly = mesh.anomaly_detector.record(
            team=team, tokens=tokens_total, cost_usd=cost_usd,
            cache_hit=False,
        )
        if anomaly and mesh.alert_router:
            mesh.alert_router.alert(
                title=f"AgentMesh Anomaly: {anomaly.anomaly_type}",
                message=anomaly.message,
                severity=anomaly.severity.value,
                team=team,
                anomaly_type=anomaly.anomaly_type,
                value=anomaly.value,
                threshold=anomaly.threshold,
            )
        if anomaly:
            hdrs["X-AgentMesh-Anomaly"] = anomaly.anomaly_type

    latency_ms = round((time.monotonic() - t0) * 1000)
    hdrs.update({
        "X-AgentMesh-Cache":      "miss",
        "X-AgentMesh-Tokens":     str(tokens_total),
        "X-AgentMesh-Cost-USD":   f"{cost_usd:.6f}",
        "X-AgentMesh-Latency-Ms": str(latency_ms),
    })

    return JSONResponse(
        content=format_response_for_client(content, model, client_fmt,
                                           input_tokens=tok_in, output_tokens=tok_out),
        headers=hdrs,
    )


# ── SSE helpers ───────────────────────────────────────────────────────────────

import json as _json

def _extract_sse_text(chunks: list) -> str:
    """
    Parse accumulated SSE byte chunks from a streaming response and return
    the concatenated assistant text. Handles both Anthropic and OpenAI SSE shapes.
    """
    text_parts = []
    for chunk in chunks:
        try:
            raw = chunk.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            try:
                data = _json.loads(payload)
            except Exception:
                continue
            # Anthropic shape: content_block_delta
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))
            # OpenAI shape: choices[0].delta.content
            choices = data.get("choices", [])
            if choices:
                c = choices[0].get("delta", {}).get("content")
                if c:
                    text_parts.append(c)
    return "".join(text_parts)


# ── Mesh factory ──────────────────────────────────────────────────────────────

def _build_mesh(config: ProxyConfig) -> Any:
    from agentmesh.core import AgentMesh, AgentMeshConfig
    from agentmesh.quota.engine import QuotaPolicy
    qp = QuotaPolicy(
        global_monthly_tokens=config.global_monthly_tokens,
        team_monthly_tokens=config.team_monthly_tokens,
        warn_at_pct=config.quota_warn_pct,
        hard_stop_at_pct=config.quota_hard_stop_pct,
        auto_escalate=config.auto_escalate,
        temp_grant_tokens=config.temp_grant_tokens,
    )
    cfg = AgentMeshConfig(
        enable_caching=config.enable_cache,
        cache_similarity_threshold=config.cache_threshold,
        enable_compression=config.enable_compression,
        enable_quota=True,
        quota_policy=qp,
        enable_multi_vendor=len(config.vendors) > 1,
        vendors=config.vendors if len(config.vendors) > 1 else None,
        routing_strategy=config.routing_strategy,
        log_level="WARNING",
    )
    mesh = AgentMesh(config=cfg, quota_policy=qp,
                     vendors=config.vendors if len(config.vendors) > 1 else None)

    # ── Enterprise security / monitoring modules ──────────────────────────────
    from agentmesh.security.pii_scanner import PIIScanner, ScanMode
    from agentmesh.security.injection_detector import InjectionDetector
    from agentmesh.security.toxicity_filter import ToxicityFilter
    from agentmesh.monitoring.anomaly_detector import AnomalyDetector
    from agentmesh.integrations.webhooks import AlertRouter, SlackConfig, PagerDutyConfig
    from agentmesh.integrations.saml_handler import SSOIdentityExtractor

    mesh.pii_scanner = (
        PIIScanner(
            mode=ScanMode(config.pii_mode),
            enabled_types=config.pii_entity_types or None,
        )
        if config.pii_mode else None
    )
    mesh.injection_detector = (
        InjectionDetector(block_on={"high"} if config.block_injections else set())
        if config.block_injections else None
    )
    mesh.toxicity_filter = ToxicityFilter() if config.toxicity_filter else None
    mesh.anomaly_detector = AnomalyDetector() if config.anomaly_detection else None
    mesh.sso_extractor = SSOIdentityExtractor() if config.sso_enabled else None

    slack  = SlackConfig(webhook_url=config.slack_webhook)   if config.slack_webhook   else None
    pager  = PagerDutyConfig(routing_key=config.pagerduty_key) if config.pagerduty_key else None
    mesh.alert_router = AlertRouter(slack=slack, pagerduty=pager) if (slack or pager) else None

    # Optionally swap in Redis cache backend
    if config.redis_url and mesh.cache:
        from agentmesh.cache.redis_backend import RedisCache
        redis_cache = RedisCache(url=config.redis_url)
        # Monkey-patch get/put onto the existing CostOptimizer cache slot
        mesh.cache.get  = redis_cache.get
        mesh.cache.put  = redis_cache.put
        mesh.cache.stats = redis_cache.stats()

    return mesh


# ── Background thread launcher ────────────────────────────────────────────────

_proxy_thread: Optional[threading.Thread] = None


def start_proxy(config: Optional[ProxyConfig] = None, **kwargs) -> threading.Thread:
    """
    Start the proxy in a background daemon thread.

    Example:
        start_proxy(port=8080, vendors=["anthropic","openai","google"], demo_mode=True)
    """
    global _proxy_thread
    import uvicorn
    cfg = config or ProxyConfig(**{k: v for k, v in kwargs.items()
                                   if k in ProxyConfig.__dataclass_fields__})
    proxy_app = build_proxy_app(cfg)

    def _run():
        uvicorn.run(proxy_app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)

    _proxy_thread = threading.Thread(target=_run, name="agentmesh-proxy", daemon=True)
    _proxy_thread.start()
    return _proxy_thread
