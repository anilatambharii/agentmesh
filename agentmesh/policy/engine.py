"""Policy engine — loads and evaluates AgentMesh policies."""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Optional, Union

from agentmesh.policy.schema import PolicySchema


class Policy:
    """
    Loads and evaluates AgentMesh governance policies.

    Usage:
        policy = Policy.from_yaml("agentmesh-policy.yaml")
        policy = Policy.from_dict({...})
        policy = Policy.default()
    """

    def __init__(self, schema: PolicySchema):
        self._schema = schema

    @property
    def name(self) -> str:
        return self._schema.name

    @property
    def schema(self) -> PolicySchema:
        return self._schema

    @classmethod
    def from_yaml(cls, path_or_str: Union[str, Path]) -> "Policy":
        """Load policy from a YAML file path or YAML string."""
        path = Path(path_or_str)
        if path.exists():
            content = path.read_text()
        else:
            content = str(path_or_str)

        data = yaml.safe_load(content)

        # Support top-level `policies:` list or single policy dict
        if "policies" in data:
            data = data["policies"][0]

        return cls(PolicySchema(**data))

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        return cls(PolicySchema(**data))

    @classmethod
    def default(cls) -> "Policy":
        """Sensible defaults — warn-only, no hard stops."""
        return cls.from_dict({
            "name": "agentmesh-default",
            "budget": {
                "per_run_tokens": 200_000,
                "hard_stop": False,
            },
            "circuit_breaker": {
                "max_iterations": 50,
            },
        })

    def get_model_for_context(self, context: dict) -> str:
        """Evaluate routing rules and return the appropriate model."""
        routing = self._schema.model_routing

        for trigger in routing.upgrade_triggers:
            if self._evaluate_condition(trigger.condition, context):
                model = trigger.model
                if routing.max_allowed:
                    # Respect the ceiling
                    model = self._min_model(model, routing.max_allowed)
                return model

        return routing.default

    def _evaluate_condition(self, condition: str, context: dict) -> bool:
        """Simple condition evaluator. Extend with OPA for production."""
        try:
            return bool(eval(condition, {}, context))  # noqa: S307
        except Exception:
            return False

    def _min_model(self, requested: str, maximum: str) -> str:
        """Return the less capable of two models based on a tier ranking."""
        tier_order = [
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
            "gpt-4o-mini",
            "claude-sonnet-4-6",
            "gpt-4o",
            "claude-opus-4-7",
            "gpt-4",
            "o3",
        ]
        req_idx = next((i for i, m in enumerate(tier_order) if requested in m), 999)
        max_idx = next((i for i, m in enumerate(tier_order) if maximum in m), 999)
        return requested if req_idx <= max_idx else maximum
