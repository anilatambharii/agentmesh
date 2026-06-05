"""Token budget enforcer — the core cost control primitive."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agentmesh.policy.engine import Policy

logger = logging.getLogger(__name__)

# Approximate cost per 1M tokens (input) as of June 2026
MODEL_COST_PER_1M_INPUT = {
    "claude-haiku-4-5": 0.80,
    "claude-haiku-4-5-20251001": 0.80,
    "claude-sonnet-4-6": 3.00,
    "claude-opus-4-7": 15.00,
    "gpt-4o-mini": 0.15,
    "gpt-4o": 2.50,
    "gemini-1.5-flash": 0.075,
    "gemini-1.5-pro": 1.25,
}


class BudgetExceededError(Exception):
    """Raised when an agent run exceeds its configured token budget."""

    def __init__(self, budget_type: str, used: int, limit: int):
        self.budget_type = budget_type
        self.used = used
        self.limit = limit
        super().__init__(
            f"Budget exceeded: {budget_type} used {used:,} / limit {limit:,}"
        )


@dataclass
class BudgetState:
    tokens_used: int = 0
    cost_usd: float = 0.0
    call_count: int = 0
    run_start: float = field(default_factory=time.time)
    run_tokens: int = 0


class BudgetEnforcer:
    """
    Enforces token budget policies before and after each LLM call.

    Raises BudgetExceededError when limits are exceeded and
    policy.hard_stop is True. Warns otherwise.
    """

    def __init__(self, policy: Policy):
        self.policy = policy
        self._state = BudgetState()

    @property
    def tokens_used(self) -> int:
        return self._state.tokens_used

    @property
    def cost_usd(self) -> float:
        return self._state.cost_usd

    @property
    def tokens_remaining(self) -> Optional[int]:
        limit = self.policy.schema.budget.per_run_tokens
        if limit is None:
            return None
        return max(0, limit - self._state.run_tokens)

    def remaining_ratio(self) -> float:
        """Returns 0.0 (empty) to 1.0 (full budget remaining)."""
        limit = self.policy.schema.budget.per_run_tokens
        if not limit:
            return 1.0
        used = self._state.run_tokens
        return max(0.0, 1.0 - (used / limit))

    def check_pre_call(self, kwargs: Dict[str, Any]) -> None:
        """Check budget limits before making an LLM call."""
        budget = self.policy.schema.budget

        if budget.per_run_tokens and self._state.run_tokens >= budget.per_run_tokens:
            msg = f"Per-run token budget ({budget.per_run_tokens:,}) reached before call"
            if budget.hard_stop:
                raise BudgetExceededError("per_run_tokens", self._state.run_tokens, budget.per_run_tokens)
            logger.warning(msg)

        if budget.daily_tokens and self._state.tokens_used >= budget.daily_tokens:
            msg = f"Daily token budget ({budget.daily_tokens:,}) reached"
            if budget.hard_stop:
                raise BudgetExceededError("daily_tokens", self._state.tokens_used, budget.daily_tokens)
            logger.warning(msg)

    def record_usage(self, result: Any) -> None:
        """Record token usage from an LLM response."""
        tokens = self._extract_tokens(result)
        if not tokens:
            return

        input_tokens = tokens.get("input_tokens", 0) or tokens.get("prompt_tokens", 0)
        output_tokens = tokens.get("output_tokens", 0) or tokens.get("completion_tokens", 0)
        total = input_tokens + output_tokens

        self._state.tokens_used += total
        self._state.run_tokens += total
        self._state.call_count += 1

        model = self._extract_model(result)
        cost_per_1m = MODEL_COST_PER_1M_INPUT.get(model, 3.0)
        self._state.cost_usd += (input_tokens / 1_000_000) * cost_per_1m

        logger.debug(
            "Token usage: +%d tokens (total: %d, cost: $%.4f)",
            total, self._state.tokens_used, self._state.cost_usd,
        )

    def reset_run(self) -> None:
        """Reset per-run counters for a new agent invocation."""
        self._state.run_tokens = 0
        self._state.run_start = time.time()

    def _extract_tokens(self, result: Any) -> Optional[Dict]:
        """Extract token usage from various provider response formats."""
        # Anthropic SDK
        if hasattr(result, "usage"):
            usage = result.usage
            return {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
            }
        # OpenAI SDK
        if hasattr(result, "usage") and hasattr(result.usage, "prompt_tokens"):
            return {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
            }
        # Dict response
        if isinstance(result, dict) and "usage" in result:
            return result["usage"]
        return None

    def _extract_model(self, result: Any) -> str:
        if hasattr(result, "model"):
            return result.model or ""
        if isinstance(result, dict):
            return result.get("model", "")
        return ""
