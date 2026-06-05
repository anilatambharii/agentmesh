"""Dynamic model router — routes to cheaper models as budget is consumed."""

from __future__ import annotations

import logging
from typing import Any, Dict

from agentmesh.policy.engine import Policy

logger = logging.getLogger(__name__)


class ModelRouter:
    """
    Dynamically routes LLM calls to appropriate model tiers based on:
    - Task complexity signals
    - Remaining budget ratio
    - Policy ceiling constraints

    Integrates RouteLLM-style routing without requiring a separate service.
    """

    def __init__(self, policy: Policy):
        self.policy = policy
        self.upgrade_count = 0
        self.downgrade_count = 0

    def route(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Inspect kwargs and potentially swap the model based on policy."""
        routing = self.policy.schema.model_routing
        current_model = kwargs.get("model", routing.default)

        context = self._extract_context_signals(kwargs)
        optimal_model = self.policy.get_model_for_context(context)

        if optimal_model != current_model:
            original = current_model
            kwargs["model"] = optimal_model
            if self._is_upgrade(original, optimal_model):
                self.upgrade_count += 1
                logger.info("Model upgraded: %s → %s (context: %s)", original, optimal_model, context)
            else:
                self.downgrade_count += 1
                logger.debug("Model downgraded: %s → %s", original, optimal_model)

        return kwargs

    def route_by_budget(self, kwargs: Dict[str, Any], remaining_ratio: float) -> Dict[str, Any]:
        """Force downgrade when budget is running low."""
        routing = self.policy.schema.model_routing

        if remaining_ratio < 0.20:
            economy_model = "claude-haiku-4-5"
            if kwargs.get("model") != economy_model:
                logger.warning(
                    "Budget at %.0f%% — forcing economy model (%s)",
                    remaining_ratio * 100, economy_model,
                )
                kwargs["model"] = economy_model
                self.downgrade_count += 1

        return kwargs

    def _extract_context_signals(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Heuristic complexity signals from the request."""
        messages = kwargs.get("messages", [])
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)

        return {
            "task_complexity": min(1.0, total_chars / 10_000),
            "requires_reasoning": any(
                kw in str(messages).lower()
                for kw in ["reason", "analyze", "explain", "compare", "evaluate"]
            ),
            "message_count": len(messages),
        }

    def _is_upgrade(self, from_model: str, to_model: str) -> bool:
        tier_order = [
            "haiku", "flash", "mini",
            "sonnet", "gpt-4o",
            "opus", "gpt-4", "o3",
        ]
        from_tier = next((i for i, t in enumerate(tier_order) if t in from_model.lower()), 0)
        to_tier = next((i for i, t in enumerate(tier_order) if t in to_model.lower()), 0)
        return to_tier > from_tier
