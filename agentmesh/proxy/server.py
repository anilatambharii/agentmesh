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

import asyncio
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
    otel_endpoint:         str             = ""     # OTLP collector, e.g. http://localhost:4317
    # ── Human-in-the-loop approval ────────────────────────────────────────────
    approval_min_cost_usd: float           = 0.0    # 0 = disabled; blanket "any call over $X needs approval"
    approval_tools:        List[str]       = field(default_factory=list)  # glob patterns needing approval regardless of cost
    approval_timeout_seconds: int          = 900
    approval_timeout_action: str           = "deny"  # "deny" | "allow" if nobody responds in time
    # ── Per-agent virtual API keys ─────────────────────────────────────────────
    virtual_keys_enabled:  bool            = False
    virtual_keys_store:    str             = ""     # JSON file to persist key hashes across restarts


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
                "otel_enabled":      bool(mesh.otel_exporter and mesh.otel_exporter.available),
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

    @app.get("/v1/approvals")
    async def list_approvals():
        if not mesh.approval_gateway:
            return JSONResponse(status_code=404, content={"error": "No approval rules configured on this proxy."})
        return mesh.approval_gateway.summary()

    @app.get("/v1/approvals/{approval_id}")
    async def get_approval(approval_id: str):
        if not mesh.approval_gateway:
            return JSONResponse(status_code=404, content={"error": "No approval rules configured on this proxy."})
        req = mesh.approval_gateway.get(approval_id)
        if not req:
            return JSONResponse(status_code=404, content={"error": f"No such approval request: {approval_id}"})
        return req.to_dict()

    @app.post("/v1/approvals/{approval_id}/approve")
    async def approve_approval(approval_id: str, request: Request):
        if not mesh.approval_gateway:
            return JSONResponse(status_code=404, content={"error": "No approval rules configured on this proxy."})
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        try:
            req = mesh.approval_gateway.approve(
                approval_id,
                approved_by=body.get("approved_by", "admin"),
                notes=body.get("notes", ""),
            )
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        bus.emit(GovernanceEvent(kind="approval_resolved", team=req.team, tool=req.tool,
                                  message=f"{req.id} approved by {req.decided_by}"))
        return req.to_dict()

    @app.post("/v1/approvals/{approval_id}/deny")
    async def deny_approval(approval_id: str, request: Request):
        if not mesh.approval_gateway:
            return JSONResponse(status_code=404, content={"error": "No approval rules configured on this proxy."})
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        try:
            req = mesh.approval_gateway.deny(
                approval_id,
                approved_by=body.get("approved_by", "admin"),
                notes=body.get("notes", ""),
            )
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        bus.emit(GovernanceEvent(kind="approval_resolved", team=req.team, tool=req.tool,
                                  message=f"{req.id} denied by {req.decided_by}"))
        return req.to_dict()

    @app.post("/v1/keys")
    async def create_key(request: Request):
        if not mesh.key_manager:
            return JSONResponse(status_code=404, content={"error": "Virtual keys not enabled on this proxy."})
        body = await request.json()
        agent_id = body.get("agent_id")
        if not agent_id:
            return JSONResponse(status_code=400, content={"error": "agent_id is required"})
        issued = mesh.key_manager.create(
            agent_id=agent_id, team=body.get("team", ""), user=body.get("user", ""),
            tool=body.get("tool", ""), scopes=body.get("scopes"), description=body.get("description", ""),
        )
        return {"key": issued.key, "warning": "Store this now — it cannot be shown again.",
                **issued.record.to_dict()}

    @app.get("/v1/keys")
    async def list_keys(team: str = "", agent_id: str = ""):
        if not mesh.key_manager:
            return JSONResponse(status_code=404, content={"error": "Virtual keys not enabled on this proxy."})
        records = mesh.key_manager.list(team=team or None, agent_id=agent_id or None)
        return {"keys": [r.to_dict() for r in records]}

    @app.post("/v1/keys/{key_id}/revoke")
    async def revoke_key(key_id: str, request: Request):
        if not mesh.key_manager:
            return JSONResponse(status_code=404, content={"error": "Virtual keys not enabled on this proxy."})
        body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        try:
            record = mesh.key_manager.revoke(key_id, reason=body.get("reason", ""))
        except ValueError as e:
            return JSONResponse(status_code=404, content={"error": str(e)})
        return record.to_dict()

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

    # ── 0.4. Virtual key resolution ───────────────────────────────────────────
    # A caller authenticating with an amk_live_... virtual key gets its identity
    # (agent/team/tool/scope) from the key record, not from spoofable headers —
    # and the raw virtual key is never forwarded upstream as a vendor credential;
    # the proxy falls back to its own server-side vendor key for the real call.
    if mesh.key_manager and api_key and api_key.startswith("amk_live_"):
        vkey = mesh.key_manager.resolve(api_key)
        if not vkey:
            return JSONResponse(
                status_code=401,
                content={"error": {"type": "invalid_virtual_key",
                                   "message": "Unknown or revoked virtual key."}},
            )
        team = vkey.team or team
        user = vkey.user or user
        tool = vkey.tool or tool
        identity = identity.__class__(user=user, team=team, tool=tool)
        api_key = None
        hdrs["X-AgentMesh-Team"]     = team
        hdrs["X-AgentMesh-Agent-Id"] = vkey.agent_id
        if not vkey.allows(tool):
            return JSONResponse(
                status_code=403,
                content={"error": {"type": "scope_denied",
                                   "message": f"Virtual key {vkey.key_id} is not scoped for tool '{tool}'"}},
            )

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

        # A dry-run miss deliberately skips real generation to stay fast — but
        # that means it would never populate the cache, so a repeat of the
        # exact same prompt would miss forever. Warm the cache in the
        # background instead: the caller gets the instant preview now, and a
        # second identical dry-run shortly after gets a real cache hit,
        # without ever blocking on the generation itself.
        if mesh.cache and cache_key:
            bg_vendor = config.vendors[0] if config.vendors else "anthropic"
            bg_demo   = config.demo_mode or not has_api_key(bg_vendor, api_key)
            asyncio.create_task(_background_warm_cache(
                mesh=mesh, cache_key=cache_key, vendor=bg_vendor, model=model_hint,
                messages=messages, max_tokens=max_tokens, temperature=temperature,
                api_key=api_key, demo_mode=bg_demo,
            ))

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

    # ── 5.7. Human-in-the-loop approval gate ──────────────────────────────────
    # Not a blocking wait: a matched call is parked as PENDING and the caller
    # gets a 202 back immediately. Once a human resolves it (dashboard, Slack
    # button, or `agentmesh approval approve`), the caller resubmits the exact
    # same request with X-AgentMesh-Approval-Id to proceed.
    if mesh.approval_gateway:
        from agentmesh.approval.gateway import ApprovalStatus
        approval_id = request.headers.get("X-AgentMesh-Approval-Id", "")
        approved_inline = False
        if approval_id:
            existing = mesh.approval_gateway.get(approval_id)
            if existing is None:
                hdrs["X-AgentMesh-Approval-Status"] = "unknown"
                return JSONResponse(
                    status_code=404, headers=hdrs,
                    content={"error": {"type": "approval_not_found",
                                       "message": f"No such approval request: {approval_id}"}},
                )
            if existing.status == ApprovalStatus.APPROVED:
                approved_inline = True
                hdrs["X-AgentMesh-Approval-Id"] = approval_id
                hdrs["X-AgentMesh-Approval-Status"] = "approved"
            elif existing.status in (ApprovalStatus.DENIED, ApprovalStatus.EXPIRED):
                hdrs["X-AgentMesh-Approval-Status"] = existing.status.value
                return JSONResponse(
                    status_code=403, headers=hdrs,
                    content={"error": {"type": "approval_denied",
                                       "message": f"Request {approval_id} was {existing.status.value}",
                                       "notes": existing.notes}},
                )
            else:
                hdrs["X-AgentMesh-Approval-Status"] = "pending"
                return JSONResponse(
                    status_code=202, headers=hdrs,
                    content={"status": "pending_approval", "approval_id": approval_id,
                             "message": "Still awaiting a human decision."},
                )

        if not approved_inline:
            from agentmesh.optimizer.multi_vendor import (VENDOR_CATALOG,
                _complexity_score as _est_score, _tier_from_score as _est_tier)
            score, _ = _est_score(cache_key)
            cat = VENDOR_CATALOG.get(vendor, {}).get(_est_tier(score), {})
            est_in  = estimate_input_tokens(internal)
            est_out = max_tokens
            est_cost = (est_in / 1_000_000) * cat.get("input_per_1m", 0) + \
                       (est_out / 1_000_000) * cat.get("output_per_1m", 0)

            decision = mesh.approval_gateway.evaluate(team=team, tool=tool, cost_usd=est_cost, tokens=est_in + est_out)
            if decision.requires_approval:
                areq = mesh.approval_gateway.request(
                    team=team, user=user, tool=tool,
                    description=f"{model} call via {tool} — ~{est_in + est_out:,} tokens, ~${est_cost:.4f}",
                    cost_usd=est_cost, tokens=est_in + est_out, rule=decision.rule,
                )
                _emit("approval_required", model=model, vendor=vendor, cost_usd=est_cost,
                      message=f"{decision.reason} — {areq.id}")
                hdrs["X-AgentMesh-Approval-Id"]     = areq.id
                hdrs["X-AgentMesh-Approval-Status"] = "pending"
                return JSONResponse(
                    status_code=202, headers=hdrs,
                    content={
                        "status": "pending_approval",
                        "approval_id": areq.id,
                        "reason": decision.reason,
                        "message": (
                            f"This call requires human approval. Resolve via "
                            f"POST /v1/approvals/{areq.id}/approve (or /deny), then resubmit "
                            f"this exact request with header 'X-AgentMesh-Approval-Id: {areq.id}'."
                        ),
                    },
                )

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


async def _background_warm_cache(
    mesh: Any,
    cache_key: str,
    vendor: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    api_key: Optional[str],
    demo_mode: bool,
) -> None:
    """
    Fire-and-forget: generate a real response for a dry-run cache miss and
    store it, so a subsequent identical prompt is a genuine cache hit.
    Never raises — this runs detached from the request that triggered it,
    so there is no caller left to hand an exception to.
    """
    try:
        raw = await forward(
            vendor=vendor, model=model, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
            client_key=api_key, demo_mode=demo_mode,
        )
        content, tok_in, tok_out = extract_response_content(raw, vendor)
        tokens_total = tok_in + tok_out
        if mesh.cache and content and tokens_total > 0 and not raw.get("_error"):
            mesh.cache.put(cache_key, raw, model=model, tokens=tokens_total)
    except Exception as e:
        logger.debug("Background cache warm failed for vendor=%s model=%s: %s", vendor, model, e)


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
    from agentmesh.policy.engine import Policy
    from agentmesh.quota.engine import QuotaPolicy
    qp = QuotaPolicy(
        global_monthly_tokens=config.global_monthly_tokens,
        team_monthly_tokens=config.team_monthly_tokens,
        warn_at_pct=config.quota_warn_pct,
        hard_stop_at_pct=config.quota_hard_stop_pct,
        auto_escalate=config.auto_escalate,
        temp_grant_tokens=config.temp_grant_tokens,
    )

    approval_rules = []
    if config.approval_min_cost_usd > 0:
        approval_rules.append({"name": "cost-threshold", "min_cost_usd": config.approval_min_cost_usd})
    if config.approval_tools:
        approval_rules.append({"name": "gated-tools", "tool_patterns": config.approval_tools})
    policy = Policy.from_dict({
        "name": "agentmesh-proxy-policy",
        "budget": {"per_run_tokens": 200_000, "hard_stop": False},
        "approval": {
            "rules": approval_rules,
            "timeout_seconds": config.approval_timeout_seconds,
            "timeout_action": config.approval_timeout_action,
        },
    })

    cfg = AgentMeshConfig(
        policy=policy,
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
            strict_pci=True,   # catch all card-shaped numbers, not just Luhn-valid ones
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

    mesh.key_manager = None
    if config.virtual_keys_enabled:
        from agentmesh.identity.keys import VirtualKeyManager
        mesh.key_manager = VirtualKeyManager(store_path=config.virtual_keys_store or None)

    slack  = SlackConfig(webhook_url=config.slack_webhook)   if config.slack_webhook   else None
    pager  = PagerDutyConfig(routing_key=config.pagerduty_key) if config.pagerduty_key else None
    mesh.alert_router = AlertRouter(slack=slack, pagerduty=pager) if (slack or pager) else None
    if mesh.approval_gateway:
        mesh.approval_gateway.alert_router = mesh.alert_router

    if config.otel_endpoint and not mesh.otel_exporter:
        from agentmesh.monitoring.otel_exporter import OTelExporter
        mesh.otel_exporter = OTelExporter(endpoint=config.otel_endpoint).start()

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
