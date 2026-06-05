"""Pydantic schema for AgentMesh policy definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class ModelTier(str, Enum):
    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


class ComplianceFramework(str, Enum):
    EU_AI_ACT = "eu-ai-act"
    NIST_AI_RMF = "nist-ai-rmf"
    HIPAA = "hipaa"
    SOC2 = "soc2"
    ISO_42001 = "iso-42001"


class BudgetConfig(BaseModel):
    daily_tokens: Optional[int] = None
    monthly_usd: Optional[float] = None
    per_workflow_tokens: Optional[int] = None
    per_run_tokens: Optional[int] = None
    hard_stop: bool = True


class ModelUpgradeTrigger(BaseModel):
    condition: str
    model: str


class ModelRoutingConfig(BaseModel):
    default: str = "claude-haiku-4-5"
    upgrade_triggers: List[ModelUpgradeTrigger] = Field(default_factory=list)
    max_allowed: Optional[str] = None
    fallback: Optional[str] = None


class OptimizationConfig(BaseModel):
    semantic_cache: bool = True
    compression_threshold: float = Field(0.75, ge=0.0, le=1.0)
    context_pruning: bool = True
    cache_ttl_seconds: int = 3600


class CircuitBreakerConfig(BaseModel):
    max_iterations: int = 30
    max_tool_calls: int = 100
    stall_detection_seconds: int = 120


class ComplianceConfig(BaseModel):
    frameworks: List[ComplianceFramework] = Field(default_factory=list)
    pii_detection: bool = False
    data_residency: Optional[str] = None


class AppliesToConfig(BaseModel):
    teams: List[str] = Field(default_factory=list)
    agent_roles: List[str] = Field(default_factory=list)
    workflow_names: List[str] = Field(default_factory=list)


class PolicySchema(BaseModel):
    name: str
    applies_to: Optional[AppliesToConfig] = None
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    model_routing: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig)
    optimization: OptimizationConfig = Field(default_factory=OptimizationConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    metadata: Dict[str, Any] = Field(default_factory=dict)
