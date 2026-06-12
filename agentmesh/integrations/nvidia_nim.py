"""
NVIDIA NIM integration — governance for NVIDIA Inference Microservices.

NIM exposes an OpenAI-compatible API. AgentMesh intercepts calls to
any NIM endpoint to enforce budget, audit, and model routing policies.

Works with:
- NVIDIA NIM (cloud API at build.nvidia.com)
- Self-hosted NIM on DGX / HGX clusters
- NVIDIA NeMo framework agents
- Llama 3, Mistral, Mixtral, and any model served via NIM

Example:
    from openai import OpenAI
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.environ["NVIDIA_API_KEY"],
    )
    governed_client = mesh.wrap_nvidia_nim(client)
    response = governed_client.chat.completions.create(
        model="meta/llama-3.1-70b-instruct",
        messages=[{"role": "user", "content": "Explain transformers"}],
    )
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Models available on NVIDIA NIM (build.nvidia.com) — June 2026
NIM_MODEL_COSTS = {
    "meta/llama-3.1-8b-instruct": 0.20,
    "meta/llama-3.1-70b-instruct": 0.99,
    "meta/llama-3.1-405b-instruct": 5.00,
    "meta/llama-3.3-70b-instruct": 0.77,
    "mistralai/mistral-7b-instruct-v0.3": 0.15,
    "mistralai/mixtral-8x7b-instruct-v0.1": 0.60,
    "nvidia/llama-3.1-nemotron-70b-instruct": 0.99,
    "google/gemma-2-27b-it": 0.40,
    "microsoft/phi-3-medium-128k-instruct": 0.42,
}


def wrap_openai_client(client: Any, mesh: Any) -> Any:
    """
    Wrap an OpenAI-compatible client (pointed at NIM) with AgentMesh governance.

    NIM's API is OpenAI-compatible, so this works by wrapping the client's
    chat.completions.create() method.
    """
    return _GovernedNIMClient(client=client, mesh=mesh)


class _GovernedNIMClient:
    """Governance proxy for NVIDIA NIM via OpenAI-compatible SDK."""

    def __init__(self, client: Any, mesh: Any):
        self._client = client
        self._mesh = mesh
        self.chat = _GovernedChatCompletions(client.chat, mesh=mesh)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _GovernedChatCompletions:
    def __init__(self, chat: Any, mesh: Any):
        self._chat = chat
        self._mesh = mesh
        self.completions = _GovernedCompletions(chat.completions, mesh=mesh)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class _GovernedCompletions:
    def __init__(self, completions: Any, mesh: Any):
        self._completions = completions
        self._mesh = mesh

    def create(self, **kwargs) -> Any:
        self._mesh.circuit_breaker.check()

        model = kwargs.get("model", "unknown-nim-model")
        if model in NIM_MODEL_COSTS:
            logger.debug("NVIDIA NIM model: %s ($%.2f/1M tokens)", model, NIM_MODEL_COSTS[model])

        if self._mesh.router:
            kwargs = self._mesh.router.route(kwargs)
        if self._mesh.compressor:
            kwargs = self._mesh.compressor.maybe_compress(kwargs, self._mesh.budget.remaining_ratio())

        self._mesh.budget.check_pre_call(kwargs)
        self._mesh.audit.record_call(kwargs)

        result = self._completions.create(**kwargs)

        self._mesh.budget.record_usage(result)
        self._mesh.audit.record_result(result)
        self._mesh.circuit_breaker.increment()

        return result

    async def acreate(self, **kwargs) -> Any:
        self._mesh.circuit_breaker.check()
        self._mesh.budget.check_pre_call(kwargs)
        self._mesh.audit.record_call(kwargs)

        result = await self._completions.create(**kwargs)

        self._mesh.budget.record_usage(result)
        self._mesh.audit.record_result(result)
        self._mesh.circuit_breaker.increment()

        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._completions, name)
