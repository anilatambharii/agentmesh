"""CrewAI integration — wraps a Crew with AgentMesh governance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agentmesh.core import AgentMesh

logger = logging.getLogger(__name__)


def wrap_crew(crew: Any, mesh: "AgentMesh") -> Any:
    """
    Wrap a CrewAI Crew with AgentMesh governance.

    Usage:
        from agentmesh.integrations.crewai import wrap_crew
        governed = wrap_crew(my_crew, mesh=mesh)
        result = governed.kickoff(inputs={...})
    """
    try:
        import crewai  # noqa: F401
    except ImportError:
        raise ImportError("crewai not installed. Run: pip install crewai")

    return _GovernedCrew(crew, mesh)


class _GovernedCrew:
    def __init__(self, crew: Any, mesh: "AgentMesh"):
        self._crew = crew
        self._mesh = mesh

    def kickoff(self, inputs: Optional[dict] = None, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        self._mesh.audit.record_delegation("user", "crewai-orchestrator", str(inputs)[:256])

        logger.info("AgentMesh: starting governed CrewAI kickoff")
        result = self._crew.kickoff(inputs=inputs, **kwargs)
        logger.info("AgentMesh stats: %s", self._mesh.stats)
        return result

    async def kickoff_async(self, inputs: Optional[dict] = None, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        return await self._crew.kickoff_async(inputs=inputs, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._crew, name)
