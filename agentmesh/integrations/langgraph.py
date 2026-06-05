"""LangGraph integration — wraps a compiled graph with AgentMesh governance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentmesh.core import AgentMesh

logger = logging.getLogger(__name__)


def wrap_graph(graph: Any, mesh: "AgentMesh") -> Any:
    """
    Wrap a compiled LangGraph graph with AgentMesh governance.

    Intercepts all model invocations within the graph to apply
    budget enforcement, audit logging, and circuit breaking.

    Usage:
        from agentmesh.integrations.langgraph import wrap_graph
        governed = wrap_graph(compiled_graph, mesh=mesh)
        result = governed.invoke({"messages": [...]})
    """
    try:
        from langgraph.graph.state import CompiledStateGraph
    except ImportError:
        raise ImportError("langgraph not installed. Run: pip install langgraph")

    return _GovernedLangGraph(graph, mesh)


class _GovernedLangGraph:
    """Thin wrapper that intercepts LangGraph execution."""

    def __init__(self, graph: Any, mesh: "AgentMesh"):
        self._graph = graph
        self._mesh = mesh

    def invoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        self._mesh.audit.record_delegation("user", "langgraph-orchestrator", str(input)[:256])

        logger.info("AgentMesh: starting governed LangGraph invocation")

        # Patch the graph's bound model calls via callback handler
        config = config or {}
        config.setdefault("callbacks", [])
        config["callbacks"].append(_AgentMeshCallback(self._mesh))

        result = self._graph.invoke(input, config=config, **kwargs)

        logger.info("AgentMesh stats: %s", self._mesh.stats)
        return result

    async def ainvoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        config = config or {}
        config.setdefault("callbacks", [])
        config["callbacks"].append(_AgentMeshCallback(self._mesh))
        return await self._graph.ainvoke(input, config=config, **kwargs)

    def stream(self, input: Any, config: Any = None, **kwargs):
        self._mesh.budget.reset_run()
        self._mesh.circuit_breaker.reset()
        config = config or {}
        config.setdefault("callbacks", [])
        config["callbacks"].append(_AgentMeshCallback(self._mesh))
        yield from self._graph.stream(input, config=config, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


class _AgentMeshCallback:
    """LangChain callback handler that intercepts LLM events."""

    def __init__(self, mesh: "AgentMesh"):
        self._mesh = mesh

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._mesh.circuit_breaker.check()
        self._mesh.budget.check_pre_call({"prompts": prompts})

    def on_llm_end(self, response, **kwargs):
        self._mesh.budget.record_usage(response)
        self._mesh.circuit_breaker.increment()

    def on_tool_start(self, serialized, input_str, **kwargs):
        tool_name = serialized.get("name", "unknown")
        self._mesh.circuit_breaker.record_tool_call()
        self._mesh.audit.record_tool_call(tool_name, "langgraph-agent", {"input": input_str[:256]})
