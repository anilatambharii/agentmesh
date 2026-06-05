"""Circuit breaker — kills runaway agent loops before they drain budgets."""

from __future__ import annotations

import logging
import time

from agentmesh.policy.engine import Policy

logger = logging.getLogger(__name__)


class CircuitBreakerError(Exception):
    """Raised when the circuit breaker trips."""

    def __init__(self, reason: str, iterations: int):
        self.reason = reason
        self.iterations = iterations
        super().__init__(f"Circuit breaker tripped: {reason} (iterations: {iterations})")


class CircuitBreaker:
    """
    Prevents runaway agentic loops by enforcing hard limits on:
    - Number of iterations (ReAct steps)
    - Number of tool calls
    - Stall detection (no progress in N seconds)

    The #1 cause of $47,000 surprise API bills in production agentic systems.
    """

    def __init__(self, policy: Policy):
        self.policy = policy
        self.iteration_count = 0
        self.tool_call_count = 0
        self._last_progress_time = time.time()

    def check(self) -> None:
        """Check all circuit breaker conditions. Call before each LLM invocation."""
        cb = self.policy.schema.circuit_breaker

        if self.iteration_count >= cb.max_iterations:
            raise CircuitBreakerError(
                f"max_iterations ({cb.max_iterations}) reached",
                self.iteration_count,
            )

        if self.tool_call_count >= cb.max_tool_calls:
            raise CircuitBreakerError(
                f"max_tool_calls ({cb.max_tool_calls}) reached",
                self.iteration_count,
            )

        stall_seconds = time.time() - self._last_progress_time
        if stall_seconds > cb.stall_detection_seconds:
            raise CircuitBreakerError(
                f"stall detected ({stall_seconds:.0f}s without progress)",
                self.iteration_count,
            )

    def increment(self) -> None:
        """Record a completed iteration."""
        self.iteration_count += 1
        self._last_progress_time = time.time()
        logger.debug("Circuit breaker: iteration %d", self.iteration_count)

    def record_tool_call(self) -> None:
        self.tool_call_count += 1

    def reset(self) -> None:
        self.iteration_count = 0
        self.tool_call_count = 0
        self._last_progress_time = time.time()
