---
title: AgentMesh — AI Agent Cost Savings Calculator
emoji: 🕸️
colorFrom: indigo
colorTo: blue
sdk: gradio
sdk_version: "4.44.0"
app_file: app.py
pinned: true
license: apache-2.0
tags:
  - ai-agents
  - llm
  - cost-optimization
  - governance
  - enterprise
  - langgraph
  - crewai
  - openai-agents
  - anthropic
  - token-budget
---

# AgentMesh — AI Agent Governance & Cost Savings Calculator

**The governance plane for AI agents — policy, budget, and audit across every framework.**

This Space demonstrates how AgentMesh reduces AI agent costs by 60–90% in enterprise deployments.

## What is AgentMesh?

AgentMesh is an open-source framework-agnostic sidecar that enforces:

- **Token Budget Enforcement** — Hard limits, no surprise bills
- **Dynamic Model Routing** — Auto-route to cheaper models as budget is consumed
- **Semantic Caching** — Cache near-duplicate queries (10–40% savings)
- **Circuit Breaker** — Kill runaway loops before they drain budgets
- **Tamper-Evident Audit Trail** — Ed25519-signed compliance for EU AI Act, HIPAA, SOC 2
- **Policy-as-Code** — YAML governance enforced at runtime, not post-hoc

## Usage

```bash
pip install agentmesh
```

```python
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

mesh = AgentMesh(policy=Policy.from_yaml("policy.yaml"))
governed_graph = mesh.wrap_langgraph(your_graph)
```

## GitHub

[github.com/anilatambharii/agentmesh](https://github.com/anilatambharii/agentmesh)
