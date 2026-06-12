"""
Haystack integration — governance for deepset's Haystack pipelines.

Works with Haystack 2.x pipelines that use LLM components.
Wraps the pipeline's run() method to enforce governance policies.

Example:
    from haystack import Pipeline
    from haystack.components.generators.chat import OpenAIChatGenerator

    pipeline = Pipeline()
    pipeline.add_component("llm", OpenAIChatGenerator(model="gpt-4o-mini"))
    governed = mesh.wrap_haystack(pipeline)
    result = governed.run({"llm": {"messages": [...]}})
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def wrap_pipeline(pipeline: Any, mesh: Any) -> Any:
    """
    Wrap a Haystack 2.x Pipeline with AgentMesh governance.

    Intercepts pipeline.run() to enforce budget, audit, and circuit breaker.
    """
    return _GovernedHaystackPipeline(pipeline=pipeline, mesh=mesh)


class _GovernedHaystackPipeline:
    """Governance proxy for Haystack 2.x Pipeline."""

    def __init__(self, pipeline: Any, mesh: Any):
        self._pipeline = pipeline
        self._mesh = mesh

    def run(self, data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        self._mesh.circuit_breaker.check()

        # Estimate tokens from input (rough heuristic for pre-call check)
        input_text = str(data)
        estimated_tokens = len(input_text) // 4
        self._mesh.budget.check_pre_call({"estimated_tokens": estimated_tokens})
        self._mesh.audit.record_call({"pipeline_input_keys": list(data.keys())})

        result = self._pipeline.run(data, **kwargs)

        self._mesh.circuit_breaker.increment()
        self._mesh.audit.record_result(result)

        return result

    async def run_async(self, data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        self._mesh.circuit_breaker.check()
        self._mesh.audit.record_call({"pipeline_input_keys": list(data.keys())})

        result = await self._pipeline.run_async(data, **kwargs)

        self._mesh.circuit_breaker.increment()
        self._mesh.audit.record_result(result)

        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pipeline, name)
