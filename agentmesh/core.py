"""Core AgentMesh class — the central governance proxy."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agentmesh.policy.engine import Policy
from agentmesh.budget.enforcer import BudgetEnforcer
from agentmesh.audit.trail import AuditTrail
from agentmesh.optimizer.router import ModelRouter
from agentmesh.optimizer.compressor import PromptCompressor
from agentmesh.optimizer.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass
class AgentMeshConfig:
    policy: Optional[Policy] = None
    audit_signing_key: Optional[str] = None
    otel_endpoint: Optional[str] = None
    enable_compression: bool = True
    enable_routing: bool = True
    enable_caching: bool = True
    log_level: str = "INFO"


class AgentMesh:
    """
    Framework-agnostic governance proxy for AI agents.

    Enforces token budgets, audit trails, and cost optimizations
    across LangGraph, CrewAI, OpenAI Agents SDK, and more —
    without requiring changes to existing agent code.

    Example:
        mesh = AgentMesh(policy=Policy.from_yaml("policy.yaml"))
        governed_graph = mesh.wrap_langgraph(graph)
    """

    def __init__(
        self,
        policy: Optional[Policy] = None,
        config: Optional[AgentMeshConfig] = None,
        audit_signing_key: Optional[str] = None,
    ):
        self.config = config or AgentMeshConfig(
            policy=policy,
            audit_signing_key=audit_signing_key,
        )
        self.policy = self.config.policy or Policy.default()
        self.budget = BudgetEnforcer(self.policy)
        self.audit = AuditTrail(signing_key=self.config.audit_signing_key)
        self.router = ModelRouter(self.policy) if self.config.enable_routing else None
        self.compressor = PromptCompressor(self.policy) if self.config.enable_compression else None
        self.circuit_breaker = CircuitBreaker(self.policy)

        logging.basicConfig(level=getattr(logging, self.config.log_level))
        logger.info("AgentMesh initialized with policy: %s", self.policy.name)

    def intercept(self, llm_call: Callable, **kwargs) -> Any:
        """
        Intercept an LLM call and apply all governance layers.
        Used internally by framework integrations.
        """
        self.circuit_breaker.check()
        self.budget.check_pre_call(kwargs)

        if self.router:
            kwargs = self.router.route(kwargs)

        if self.compressor:
            kwargs = self.compressor.maybe_compress(kwargs, self.budget.remaining_ratio())

        self.audit.record_call(kwargs)

        result = llm_call(**kwargs)

        self.budget.record_usage(result)
        self.audit.record_result(result)
        self.circuit_breaker.increment()

        return result

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

    @property
    def stats(self) -> dict:
        return {
            "tokens_used": self.budget.tokens_used,
            "tokens_remaining": self.budget.tokens_remaining,
            "cost_usd": self.budget.cost_usd,
            "iterations": self.circuit_breaker.iteration_count,
            "compressions_applied": self.compressor.compression_count if self.compressor else 0,
            "model_upgrades": self.router.upgrade_count if self.router else 0,
        }
