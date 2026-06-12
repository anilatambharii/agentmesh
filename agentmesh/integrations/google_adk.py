"""
Google ADK (Agent Development Kit) integration — governance for Google's agent framework.

Supports:
- Google ADK (google-adk / Vertex AI Agent Development Kit)
- Gemini API direct calls
- Vertex AI agents

Example:
    from google.adk.agents import Agent
    agent = Agent(model="gemini-2.0-flash", instruction="You are helpful.")
    governed = mesh.wrap_google_adk(agent)
    result = governed.run("Summarize this document")
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def wrap_agent(agent: Any, mesh: Any) -> Any:
    """Wrap a Google ADK Agent with AgentMesh governance."""
    return _GovernedGoogleADKAgent(agent=agent, mesh=mesh)


class _GovernedGoogleADKAgent:
    """Governance proxy for Google ADK / Vertex AI agents."""

    def __init__(self, agent: Any, mesh: Any):
        self._agent = agent
        self._mesh = mesh

    def run(self, prompt: str, **kwargs) -> Any:
        self._mesh.circuit_breaker.check()
        model = getattr(self._agent, "model", "gemini-unknown")
        self._mesh.budget.check_pre_call({"model": str(model), "prompt": prompt[:256]})
        self._mesh.audit.record_call({"model": str(model), "prompt": prompt[:256]})

        result = self._agent.run(prompt, **kwargs)

        self._mesh.circuit_breaker.increment()
        self._mesh.budget.record_usage(result)
        self._mesh.audit.record_result(result)

        return result

    async def run_async(self, prompt: str, **kwargs) -> Any:
        self._mesh.circuit_breaker.check()
        model = getattr(self._agent, "model", "gemini-unknown")
        self._mesh.audit.record_call({"model": str(model), "prompt": prompt[:256]})

        if hasattr(self._agent, "run_async"):
            result = await self._agent.run_async(prompt, **kwargs)
        else:
            import asyncio
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._agent.run(prompt, **kwargs)
            )

        self._mesh.circuit_breaker.increment()
        self._mesh.budget.record_usage(result)
        self._mesh.audit.record_result(result)

        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)
