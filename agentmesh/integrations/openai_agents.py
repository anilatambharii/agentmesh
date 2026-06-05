"""OpenAI Agents SDK integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentmesh.core import AgentMesh

logger = logging.getLogger(__name__)


def wrap_agent(agent: Any, mesh: "AgentMesh") -> Any:
    """
    Wrap an OpenAI Agents SDK agent with AgentMesh governance.

    Usage:
        from agentmesh.integrations.openai_agents import wrap_agent
        governed = wrap_agent(my_agent, mesh=mesh)
        result = governed.run("task description")
    """
    return _GovernedOpenAIAgent(agent, mesh)


class _GovernedOpenAIAgent:
    def __init__(self, agent: Any, mesh: "AgentMesh"):
        self._agent = agent
        self._mesh = mesh

    def run(self, task: str, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        self._mesh.audit.record_delegation("user", "openai-agent", task[:256])
        logger.info("AgentMesh: starting governed OpenAI Agent run")
        result = self._agent.run(task, **kwargs)
        logger.info("AgentMesh stats: %s", self._mesh.stats)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)
