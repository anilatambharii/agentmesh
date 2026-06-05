# AgentMesh Architecture

## Overview

AgentMesh operates as a governance sidecar — it intercepts agent framework calls
without requiring changes to your existing agent code.

## Component Map

```
AgentMesh
├── core.py              — Central AgentMesh class, orchestrates all layers
├── policy/
│   ├── schema.py        — Pydantic schema for policy definitions
│   └── engine.py        — Policy loader + evaluation engine
├── budget/
│   └── enforcer.py      — Token budget tracking + hard stop enforcement
├── audit/
│   └── trail.py         — Ed25519-signed tamper-evident audit chain
├── optimizer/
│   ├── router.py        — Dynamic model routing by task + budget
│   ├── compressor.py    — Prompt compression (LLMLingua + heuristic)
│   └── circuit_breaker.py — Runaway loop prevention
├── integrations/
│   ├── langgraph.py     — LangGraph callback-based interception
│   ├── crewai.py        — CrewAI wrapper
│   └── openai_agents.py — OpenAI Agents SDK wrapper
└── bridge/
    └── bpmn.py          — BPMN 2.0 → LangGraph migration
```

## How Budget Enforcement Works

1. `BudgetEnforcer.check_pre_call()` runs before every LLM call
2. If tokens_used >= limit AND hard_stop=True → raises `BudgetExceededError`
3. `BudgetEnforcer.record_usage()` extracts token counts from any provider's response format
4. `remaining_ratio()` feeds the compressor threshold check

## How the Circuit Breaker Works

The circuit breaker prevents runaway loops — the #1 cause of surprise $47,000 bills:

1. `CircuitBreaker.check()` is called before every LLM invocation
2. Trips if: iteration_count >= max_iterations OR tool_calls >= max_tool_calls OR stall detected
3. Raises `CircuitBreakerError` which propagates up to the framework wrapper

## Audit Chain Integrity

Each `AuditEntry` contains:
- `payload_hash`: SHA-256 of the request (not full content, for PII safety)
- `prev_hash`: SHA-256 of the previous entry (chain linking)
- `signature`: Ed25519 signature over `entry_id + timestamp + payload_hash`

`AuditTrail.verify()` walks the chain and confirms every `prev_hash` matches.

## Policy Evaluation Order

1. Check circuit breaker (immediate kill if tripped)
2. Check budget pre-call (hard stop or warn)
3. Apply model routing (swap to cheaper/better model)
4. Apply compression (if remaining_ratio < threshold)
5. Record audit entry
6. Execute LLM call
7. Record result + usage
8. Update budget state
9. Increment circuit breaker
