"""Core AgentMesh class — the central governance proxy."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agentmesh.policy.engine import Policy
from agentmesh.budget.enforcer import BudgetEnforcer
from agentmesh.audit.trail import AuditTrail
from agentmesh.optimizer.router import ModelRouter
from agentmesh.optimizer.compressor import PromptCompressor
from agentmesh.optimizer.circuit_breaker import CircuitBreaker
from agentmesh.cache.semantic import SemanticCache

logger = logging.getLogger(__name__)


@dataclass
class AgentMeshConfig:
    policy: Optional[Policy] = None
    audit_signing_key: Optional[str] = None
    otel_endpoint: Optional[str] = None
    enable_compression: bool = True
    enable_routing: bool = True
    enable_caching: bool = True
    cache_similarity_threshold: float = 0.70  # sentence-transformers threshold
    log_level: str = "INFO"

    # Token quota governance
    enable_quota: bool = False
    quota_policy: Optional[Any] = None     # QuotaPolicy
    quota_store:  Optional[Any] = None     # QuotaStore (Redis or in-memory)

    # Multi-vendor routing
    enable_multi_vendor: bool = False
    vendors: Optional[List[str]] = None    # ["anthropic","openai","google"]
    routing_strategy: str = "cheapest_capable"

    # Three-layer cost optimizer
    enable_cost_optimizer: bool = False
    cost_optimizer_threshold: float = 0.70  # sentence-transformers threshold


class AgentMesh:
    """
    Framework-agnostic governance proxy for AI agents.

    Enforces token budgets, audit trails, semantic caching, and cost
    optimizations across LangGraph, CrewAI, OpenAI Agents SDK, AutoGen,
    and more — without requiring changes to existing agent code.

    Supports both sync and async agent frameworks.

    Example:
        mesh = AgentMesh(policy=Policy.from_yaml("policy.yaml"))
        governed_graph = mesh.wrap_langgraph(graph)

        # async usage
        result = await mesh.intercept_async(my_llm_coroutine, **kwargs)
    """

    def __init__(
        self,
        policy: Optional[Policy] = None,
        config: Optional[AgentMeshConfig] = None,
        audit_signing_key: Optional[str] = None,
        # Shortcut kwargs for new governance layers
        quota_policy: Optional[Any] = None,
        vendors: Optional[List[str]] = None,
        routing_strategy: str = "cheapest_capable",
    ):
        self.config = config or AgentMeshConfig(
            policy=policy,
            audit_signing_key=audit_signing_key,
            quota_policy=quota_policy,
            vendors=vendors,
            routing_strategy=routing_strategy,
            enable_quota=quota_policy is not None,
            enable_multi_vendor=vendors is not None,
        )
        self.policy = self.config.policy or Policy.default()
        self.budget = BudgetEnforcer(self.policy)
        self.audit = AuditTrail(signing_key=self.config.audit_signing_key)
        self.router = ModelRouter(self.policy) if self.config.enable_routing else None
        self.compressor = PromptCompressor(self.policy) if self.config.enable_compression else None
        self.circuit_breaker = CircuitBreaker(self.policy)
        self.cache = (
            SemanticCache(
                similarity_threshold=self.config.cache_similarity_threshold,
                ttl_seconds=self.policy.schema.optimization.cache_ttl_seconds,
            )
            if self.config.enable_caching and self.policy.schema.optimization.semantic_cache
            else None
        )

        # ── New governance layers ──────────────────────────────────────────
        self.quota_enforcer:   Optional[Any] = None
        self.escalation_mgr:   Optional[Any] = None
        self.multi_vendor:     Optional[Any] = None
        self.cost_optimizer:   Optional[Any] = None

        if self.config.enable_quota and self.config.quota_policy is not None:
            from agentmesh.quota.engine import QuotaEnforcer
            from agentmesh.quota.escalation import EscalationManager
            self.quota_enforcer = QuotaEnforcer(
                self.config.quota_policy,
                store=self.config.quota_store,
            )
            self.escalation_mgr = EscalationManager(
                enforcer=self.quota_enforcer,
                auto_temp_grant=True,
            )

        if self.config.enable_multi_vendor:
            from agentmesh.optimizer.multi_vendor import MultiVendorRouter
            self.multi_vendor = MultiVendorRouter(
                vendors=self.config.vendors or ["anthropic", "openai", "google"],
                routing_strategy=self.config.routing_strategy,
            )

        if self.config.enable_cost_optimizer:
            from agentmesh.optimizer.cost_optimizer import CostOptimizer
            self.cost_optimizer = CostOptimizer(
                similarity_threshold=self.config.cost_optimizer_threshold,
            )

        logging.basicConfig(level=getattr(logging, self.config.log_level))
        logger.info("AgentMesh initialized with policy: %s", self.policy.name)

    # ------------------------------------------------------------------
    # Sync intercept
    # ------------------------------------------------------------------

    def intercept(self, llm_call: Callable, identity: Optional[Any] = None, **kwargs) -> Any:
        """
        Intercept a synchronous LLM call and apply all governance layers.
        Used internally by framework integrations.

        Args:
            identity: Optional QuotaIdentity (user/team/tool) for quota enforcement.
        """
        from agentmesh.events.bus import get_bus, GovernanceEvent
        bus   = get_bus()
        team  = getattr(identity, "team",  "") if identity else ""
        user  = getattr(identity, "user",  "") if identity else ""
        tool  = getattr(identity, "tool",  "") if identity else ""
        model = kwargs.get("model", "")

        def _emit(kind: str, **kw) -> None:
            try:
                bus.emit(GovernanceEvent(kind=kind, team=team, user=user,
                                         tool=tool, model=model, **kw))
            except Exception:
                pass

        # ── Quota check (FIRST — before any tokens burned) ────────────────
        if self.quota_enforcer and identity is not None:
            from agentmesh.quota.engine import QuotaStatus, QuotaExceededError
            result = self.quota_enforcer.check(identity)
            if result.status == QuotaStatus.BLOCK:
                esc_id = ""
                if self.escalation_mgr:
                    esc = self.escalation_mgr.request(
                        identity=identity,
                        quota_result=result,
                        reason="Auto-escalation: quota exceeded during LLM call",
                    )
                    esc_id = esc.id
                _emit("quota_block", quota_pct=result.pct_used,
                      quota_used=result.used_tokens, quota_limit=result.limit_tokens,
                      escalation_id=esc_id, message=result.message)
                raise QuotaExceededError(result)
            if result.status == QuotaStatus.WARN:
                logger.warning("Quota warning for %s: %s", identity.team, result.message)
                _emit("quota_warn", quota_pct=result.pct_used,
                      quota_used=result.used_tokens, quota_limit=result.limit_tokens,
                      message=result.message)

        self.circuit_breaker.check()
        self.budget.check_pre_call(kwargs)

        # Semantic cache lookup
        if self.cache:
            cached = self.cache.get(kwargs.get("messages", kwargs.get("prompt", "")))
            if cached is not None:
                logger.debug("Serving response from semantic cache (hit_rate=%.1f%%)", self.cache.hit_rate * 100)
                _emit("cache_hit", cache_layer="semantic",
                      tokens_saved=getattr(cached, "_tokens_hint", 0))
                return cached
            else:
                _emit("cache_miss", cache_layer="miss")

        if self.router:
            kwargs = self.router.route(kwargs)

        if self.compressor:
            kwargs = self.compressor.maybe_compress(kwargs, self.budget.remaining_ratio())

        self.audit.record_call(kwargs)

        result = llm_call(**kwargs)

        self.budget.record_usage(result)
        self.audit.record_result(result)
        self.circuit_breaker.increment()

        # Deduct from quota
        tokens_in  = getattr(getattr(result, "usage", None), "input_tokens",  0)
        tokens_out = getattr(getattr(result, "usage", None), "output_tokens", 0)
        tokens_used = tokens_in + tokens_out
        if self.quota_enforcer and identity is not None and tokens_used:
            self.quota_enforcer.consume(identity, tokens_used)

        # Emit LLM call event
        _emit("llm_call", tokens_used=tokens_used,
              cost_usd=self.budget.cost_usd,
              message=f"{tokens_used} tokens @ {model}")

        # Store in semantic cache
        if self.cache:
            self.cache.put(
                kwargs.get("messages", kwargs.get("prompt", "")),
                result,
                model=kwargs.get("model", "unknown"),
            )

        return result

    # ------------------------------------------------------------------
    # Async intercept (for async agent frameworks)
    # ------------------------------------------------------------------

    async def intercept_async(self, llm_coro: Callable, identity: Optional[Any] = None, **kwargs) -> Any:
        """
        Intercept an async LLM call. Works with any async agent framework
        (LangGraph async, pydantic-ai, OpenAI Agents SDK async, etc.)
        """
        # Quota check
        if self.quota_enforcer and identity is not None:
            from agentmesh.quota.engine import QuotaStatus, QuotaExceededError
            result = self.quota_enforcer.check(identity)
            if result.status == QuotaStatus.BLOCK:
                if self.escalation_mgr:
                    self.escalation_mgr.request(identity=identity, quota_result=result,
                                                  reason="Auto-escalation: async quota exceeded")
                raise QuotaExceededError(result)

        self.circuit_breaker.check()
        self.budget.check_pre_call(kwargs)

        if self.cache:
            cached = self.cache.get(kwargs.get("messages", kwargs.get("prompt", "")))
            if cached is not None:
                return cached

        if self.router:
            kwargs = self.router.route(kwargs)

        if self.compressor:
            kwargs = self.compressor.maybe_compress(kwargs, self.budget.remaining_ratio())

        self.audit.record_call(kwargs)

        if asyncio.iscoroutinefunction(llm_coro):
            result = await llm_coro(**kwargs)
        else:
            result = await asyncio.get_event_loop().run_in_executor(None, lambda: llm_coro(**kwargs))

        self.budget.record_usage(result)
        self.audit.record_result(result)
        self.circuit_breaker.increment()

        if self.quota_enforcer and identity is not None:
            tokens_used = getattr(getattr(result, "usage", None), "input_tokens", 0) + \
                          getattr(getattr(result, "usage", None), "output_tokens", 0)
            if tokens_used:
                self.quota_enforcer.consume(identity, tokens_used)

        if self.cache:
            self.cache.put(
                kwargs.get("messages", kwargs.get("prompt", "")),
                result,
                model=kwargs.get("model", "unknown"),
            )

        return result

    # ------------------------------------------------------------------
    # Framework wrappers
    # ------------------------------------------------------------------

    def wrap_langgraph(self, graph: Any) -> Any:
        from agentmesh.integrations.langgraph import wrap_graph
        return wrap_graph(graph, mesh=self)

    def wrap_crewai(self, crew: Any) -> Any:
        from agentmesh.integrations.crewai import wrap_crew
        return wrap_crew(crew, mesh=self)

    def wrap_openai_agent(self, agent: Any) -> Any:
        from agentmesh.integrations.openai_agents import wrap_agent
        return wrap_agent(agent, mesh=self)

    def wrap_pydantic_agent(self, agent: Any) -> Any:
        from agentmesh.integrations.pydantic_ai import wrap_agent
        return wrap_agent(agent, mesh=self)

    def wrap_autogen(self, agent: Any) -> Any:
        from agentmesh.integrations.autogen import wrap_agent
        return wrap_agent(agent, mesh=self)

    def wrap_haystack(self, pipeline: Any) -> Any:
        from agentmesh.integrations.haystack import wrap_pipeline
        return wrap_pipeline(pipeline, mesh=self)

    # ------------------------------------------------------------------
    # Stats & observability
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        s = {
            "tokens_used": self.budget.tokens_used,
            "tokens_remaining": self.budget.tokens_remaining,
            "cost_usd": self.budget.cost_usd,
            "iterations": self.circuit_breaker.iteration_count,
            "tool_calls": self.circuit_breaker.tool_call_count,
            "compressions_applied": self.compressor.compression_count if self.compressor else 0,
            "model_upgrades": self.router.upgrade_count if self.router else 0,
            "model_downgrades": self.router.downgrade_count if self.router else 0,
        }
        if self.cache:
            s["cache"] = self.cache.stats
        if self.quota_enforcer:
            s["quota"] = {"usage": self.quota_enforcer.usage_summary()}
        if self.escalation_mgr:
            s["escalations"] = self.escalation_mgr.summary()
        if self.cost_optimizer:
            s["cost_optimizer"] = self.cost_optimizer.stats
        return s

    def reset(self) -> None:
        """Reset per-run state (budget, circuit breaker). Call between agent runs."""
        self.budget.reset_run()
        self.circuit_breaker.reset()
        logger.debug("AgentMesh state reset for new run")
