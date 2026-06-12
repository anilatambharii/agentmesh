# Policy Reference

AgentMesh policies are defined in YAML and validated against the `PolicySchema` Pydantic model.

## Full Schema

```yaml
version: "1.0"           # required
policies:
  - name: my-policy      # required — human-readable identifier

    applies_to:           # optional — scope this policy
      teams: ["engineering", "data-science"]
      agent_roles: ["orchestrator", "worker"]
      workflow_names: ["code-review", "data-pipeline"]

    budget:
      daily_tokens: 1_000_000      # int — tokens per UTC day
      monthly_usd: 3_000           # float — USD per calendar month
      per_run_tokens: 100_000      # int — tokens per single agent run
      per_workflow_tokens: 200_000 # int — tokens per workflow execution
      hard_stop: true              # bool — kill run if exceeded (default: true)

    model_routing:
      default: "claude-haiku-4-5"  # string — model used when no trigger fires
      upgrade_triggers:
        - condition: "task_complexity > 0.8"   # fires when heuristic > threshold
          model: "claude-sonnet-4-6"
        - condition: "requires_reasoning"       # fires when reasoning keywords found
          model: "claude-sonnet-4-6"
      max_allowed: "claude-sonnet-4-6"          # never auto-upgrade beyond this
      fallback: "claude-haiku-4-5"              # use if routing fails

    optimization:
      semantic_cache: true             # bool — enable semantic cache
      compression_threshold: 0.75     # float 0-1 — compress at this budget ratio
      context_pruning: true            # bool — heuristic message pruning
      cache_ttl_seconds: 3600          # int — cache entry lifetime

    circuit_breaker:
      max_iterations: 25               # int — kill loop after N iterations
      max_tool_calls: 50               # int — kill loop after N tool calls
      stall_detection_seconds: 120     # int — kill if no progress in N seconds

    compliance:
      frameworks:
        - eu-ai-act        # EU AI Act (Article 13, 14, 17)
        - nist-ai-rmf      # NIST AI Risk Management Framework
        - hipaa            # HIPAA Security Rule
        - soc2             # SOC 2 Type II
        - iso-42001        # ISO/IEC 42001 AI Management Systems
      pii_detection: true              # bool — scan for PII before LLM calls
      data_residency: "us"             # string — geo restriction (informational)

    metadata:                          # dict — arbitrary key-value pairs
      team: "engineering"
      last_reviewed: "2026-06-01"
      reviewed_by: "platform-team"
```

## Budget Configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `daily_tokens` | int | null | Max tokens per UTC day across all calls |
| `monthly_usd` | float | null | Max spend in USD per calendar month |
| `per_run_tokens` | int | null | Max tokens for a single `mesh.intercept()` sequence |
| `per_workflow_tokens` | int | null | Max tokens for a named workflow |
| `hard_stop` | bool | `true` | If `true`: raise `BudgetExceededError`. If `false`: warn only |

When multiple limits are set, the first to be exceeded triggers the action.

## Model Routing

| Field | Type | Description |
|---|---|---|
| `default` | string | Model used when no upgrade trigger fires |
| `upgrade_triggers[].condition` | string | `"task_complexity > N"` or `"requires_reasoning"` |
| `upgrade_triggers[].model` | string | Model to use when condition is true |
| `max_allowed` | string | Hard ceiling — no auto-routing above this tier |
| `fallback` | string | Fall back to this model if routing fails |

### Complexity Signal

`task_complexity` is computed as `min(1.0, total_chars / 10_000)` where `total_chars` is the combined length of all message content. A 10,000-character conversation scores 1.0; a 100-character message scores 0.01.

### Reasoning Signal

`requires_reasoning` fires when the message text contains: `reason`, `analyze`, `explain`, `compare`, `evaluate`.

## Optimization

| Field | Type | Default | Description |
|---|---|---|---|
| `semantic_cache` | bool | `true` | Enable embedding-based response caching |
| `compression_threshold` | float | `0.75` | Apply compression when budget ratio drops below this |
| `context_pruning` | bool | `true` | Prune old messages from context window |
| `cache_ttl_seconds` | int | `3600` | How long cache entries live |

## Circuit Breaker

| Field | Type | Default | Description |
|---|---|---|---|
| `max_iterations` | int | `30` | Max LLM calls before `CircuitBreakerError` |
| `max_tool_calls` | int | `100` | Max tool/function calls before trip |
| `stall_detection_seconds` | int | `120` | Trip if no progress in N seconds |

## Compliance

| Field | Type | Description |
|---|---|---|
| `frameworks` | list | One or more of: `eu-ai-act`, `nist-ai-rmf`, `hipaa`, `soc2`, `iso-42001` |
| `pii_detection` | bool | Enable PII scanning (regex-based) |
| `data_residency` | string | Geographic region (e.g., `us`, `eu`, `us-healthcare`) — informational |

## Python API

```python
from agentmesh.policy.engine import Policy

# From YAML string
policy = Policy.from_yaml(yaml_string)

# From YAML file
policy = Policy.from_yaml(open("policy.yaml").read())

# From dict
policy = Policy.from_dict({"policies": [{"name": "test", "budget": {"per_run_tokens": 10000}}]})

# Default (permissive — use for development only)
policy = Policy.default()

# Introspect
policy.name            # "my-policy"
policy.schema.budget   # BudgetConfig(daily_tokens=None, ...)
policy.schema.circuit_breaker.max_iterations  # 25
```
