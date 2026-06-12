"""Pydantic AI integration — governance wrapper for pydantic-ai agents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def wrap_agent(agent: Any, mesh: Any) -> Any:
    """
    Wrap a pydantic-ai Agent with AgentMesh governance.

    Intercepts run() and run_sync() calls to enforce budget,
    audit, and circuit-breaker policies.

    Example:
        from pydantic_ai import Agent
        agent = Agent("claude-haiku-4-5", system_prompt="You are helpful.")
        governed = mesh.wrap_pydantic_agent(agent)
        result = governed.run_sync("What is 2+2?")
    """
    return _GovernedPydanticAgent(agent=agent, mesh=mesh)


class _GovernedPydanticAgent:
    """Proxy wrapper that intercepts pydantic-ai Agent calls."""

    def __init__(self, agent: Any, mesh: Any):
        self._agent = agent
        self._mesh = mesh

    async def run(self, prompt: str, **kwargs) -> Any:
        self._mesh.circuit_breaker.check()
        self._mesh.budget.check_pre_call({"model": getattr(self._agent, "model", "unknown")})
        self._mesh.audit.record_call({"prompt": prompt[:256], "model": str(getattr(self._agent, "model", ""))})

        result = await self._agent.run(prompt, **kwargs)

        self._mesh.budget.record_usage(result)
        self._mesh.audit.record_result(result)
        self._mesh.circuit_breaker.increment()

        return result

    def run_sync(self, prompt: str, **kwargs) -> Any:
        return asyncio.get_event_loop().run_until_complete(self.run(prompt, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)
