"""Prompt compressor — auto-compress context when approaching budget limits."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agentmesh.policy.engine import Policy

logger = logging.getLogger(__name__)


class PromptCompressor:
    """
    Automatically compresses prompts when the remaining budget ratio
    drops below the configured threshold.

    Integrates with LLMLingua when available; falls back to a
    heuristic context pruner that is dependency-free.
    """

    def __init__(self, policy: Policy):
        self.policy = policy
        self.compression_count = 0
        self._llmlingua_available = self._check_llmlingua()

    def maybe_compress(self, kwargs: Dict[str, Any], remaining_ratio: float) -> Dict[str, Any]:
        """Compress the prompt if budget is below the configured threshold."""
        threshold = self.policy.schema.optimization.compression_threshold

        if remaining_ratio > threshold:
            return kwargs

        logger.info(
            "Budget at %.0f%% — applying prompt compression (threshold: %.0f%%)",
            remaining_ratio * 100, threshold * 100,
        )

        if self._llmlingua_available:
            kwargs = self._compress_with_llmlingua(kwargs)
        else:
            kwargs = self._compress_heuristic(kwargs)

        self.compression_count += 1
        return kwargs

    def _compress_heuristic(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dependency-free context pruner.
        Removes older middle messages while preserving system prompt,
        first user message, and last N exchanges.
        """
        messages: List[Dict] = kwargs.get("messages", [])
        if len(messages) <= 6:
            return kwargs

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Keep first exchange + last 4 messages, prune the middle
        if len(non_system) > 6:
            preserved = non_system[:2] + non_system[-4:]
            pruned_count = len(non_system) - len(preserved)
            non_system = preserved
            logger.debug("Heuristic compression: pruned %d messages", pruned_count)

        kwargs["messages"] = system_msgs + non_system
        return kwargs

    def _compress_with_llmlingua(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLMLingua for higher-quality compression when available."""
        try:
            from llmlingua import PromptCompressor as LLMLinguaCompressor

            compressor = LLMLinguaCompressor(
                model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
                use_llmlingua2=True,
            )
            messages = kwargs.get("messages", [])
            prompt = " ".join(str(m.get("content", "")) for m in messages)
            compressed = compressor.compress_prompt(prompt, rate=0.5)
            # Reconstruct a single user message with compressed content
            system = [m for m in messages if m.get("role") == "system"]
            kwargs["messages"] = system + [{"role": "user", "content": compressed["compressed_prompt"]}]
            logger.debug("LLMLingua compression ratio: %.2f", compressed.get("ratio", 0))
        except Exception as e:
            logger.warning("LLMLingua compression failed (%s), using heuristic", e)
            kwargs = self._compress_heuristic(kwargs)
        return kwargs

    def _check_llmlingua(self) -> bool:
        try:
            import llmlingua  # noqa: F401
            return True
        except ImportError:
            return False
