"""AgentMesh — The governance plane for AI agents."""

from agentmesh.core import AgentMesh, AgentMeshConfig
from agentmesh.policy.engine import Policy
from agentmesh.budget.enforcer import BudgetEnforcer, BudgetExceededError
from agentmesh.audit.trail import AuditTrail
from agentmesh.optimizer.circuit_breaker import CircuitBreaker, CircuitBreakerError
from agentmesh.cache.semantic import SemanticCache
from agentmesh.attribution.chargebacks import CostAttributor
from agentmesh.compliance.reporter import ComplianceReporter
from agentmesh.quota.engine import QuotaPolicy, QuotaEnforcer, QuotaIdentity, QuotaCheckResult
from agentmesh.quota.escalation import EscalationManager
from agentmesh.optimizer.multi_vendor import MultiVendorRouter
from agentmesh.optimizer.cost_optimizer import CostOptimizer
from agentmesh.events.bus import EventBus, GovernanceEvent, get_bus
from agentmesh.server import start_server, get_app as get_observability_app

__version__ = "0.2.0"
__author__ = "Anil Prasad"
__license__ = "Apache-2.0"
__all__ = [
    # Core
    "AgentMesh",
    "AgentMeshConfig",
    # Policy
    "Policy",
    # Budget
    "BudgetEnforcer",
    "BudgetExceededError",
    # Audit
    "AuditTrail",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitBreakerError",
    # Cache
    "SemanticCache",
    # Attribution
    "CostAttributor",
    # Compliance
    "ComplianceReporter",
    # Token Quota
    "QuotaPolicy",
    "QuotaEnforcer",
    "QuotaIdentity",
    "QuotaCheckResult",
    # Escalation
    "EscalationManager",
    # Multi-vendor routing
    "MultiVendorRouter",
    # Cost optimizer
    "CostOptimizer",
    # Event bus (real-time streaming)
    "EventBus",
    "GovernanceEvent",
    "get_bus",
    # Observability server
    "start_server",
    "get_observability_app",
]
