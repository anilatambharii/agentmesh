"""
AutoGen v2 / AG2 integration — governance for Microsoft's multi-agent framework.

Works with both AutoGen 0.4+ (python-autogen) and AG2 (agentchat).
Intercepts LLM calls in ConversableAgent to enforce budgets and audit trails.

Example:
    import autogen
    assistant = autogen.AssistantAgent("assistant", llm_config=llm_config)
    governed = mesh.wrap_autogen(assistant)
    governed.initiate_chat(user_proxy, message="Write a report on Q2 sales")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def wrap_agent(agent: Any, mesh: Any) -> Any:
    """
    Wrap an AutoGen ConversableAgent with AgentMesh governance.

    Supports:
    - AutoGen 0.4+ (microsoft/autogen)
    - AG2 / agentchat fork
    - Microsoft Semantic Kernel agent mode

    Zero changes needed to existing agent config or system prompts.
    """
    return _GovernedAutoGenAgent(agent=agent, mesh=mesh)


class _GovernedAutoGenAgent:
    """Governance proxy for AutoGen ConversableAgent and GroupChatManager."""

    def __init__(self, agent: Any, mesh: Any):
        self._agent = agent
        self._mesh = mesh
        self._patch_generate_reply()

    def _patch_generate_reply(self) -> None:
        """Monkey-patch generate_reply to intercept LLM calls."""
        original = getattr(self._agent, "generate_reply", None)
        if original is None:
            return

        mesh = self._mesh
        _self = self

        def governed_generate_reply(messages=None, sender=None, **kwargs):
            mesh.circuit_breaker.check()
            call_kwargs = {"model": _self._extract_model(), "messages": messages or []}
            mesh.budget.check_pre_call(call_kwargs)
            mesh.audit.record_call(call_kwargs, agent_id=getattr(agent, "name", "autogen-agent"))

            result = original(messages=messages, sender=sender, **kwargs)

            mesh.circuit_breaker.increment()
            mesh.audit.record_result(result)
            return result

        agent = self._agent
        self._agent.generate_reply = governed_generate_reply
        logger.debug("AutoGen agent '%s' wrapped with AgentMesh governance", getattr(agent, "name", "?"))

    def _extract_model(self) -> str:
        cfg = getattr(self._agent, "llm_config", {}) or {}
        if isinstance(cfg, dict):
            for item in cfg.get("config_list", []):
                if isinstance(item, dict) and "model" in item:
                    return item["model"]
            return cfg.get("model", "unknown")
        return "unknown"

    def initiate_chat(self, recipient: Any, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        return self._agent.initiate_chat(recipient, **kwargs)

    async def a_initiate_chat(self, recipient: Any, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        return await self._agent.a_initiate_chat(recipient, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)
