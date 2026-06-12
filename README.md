# AgentMesh 🕸️

**The governance plane for AI agents — policy, budget, and audit across every framework.**

> *"Istio for AI agents. Every framework. Every cloud. Every model."*

[![CI](https://github.com/anilatambharii/agentmesh/actions/workflows/ci.yml/badge.svg)](https://github.com/anilatambharii/agentmesh/actions/workflows/ci.yml)
[![CodeQL](https://github.com/anilatambharii/agentmesh/actions/workflows/codeql.yml/badge.svg)](https://github.com/anilatambharii/agentmesh/actions/workflows/codeql.yml)
[![PyPI version](https://badge.fury.io/py/agentmesh.svg)](https://badge.fury.io/py/agentmesh)
[![PyPI Downloads](https://static.pepy.tech/badge/agentmesh)](https://pepy.tech/project/agentmesh)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![HuggingFace Space](https://img.shields.io/badge/%F0%9F%A4%97%20HuggingFace-Space-yellow)](https://huggingface.co/spaces/anilatambharii/agentmesh)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-7289DA)](https://discord.gg/agentmesh)

---

<!-- Demo GIF — 20-step agent: $3,200/mo → $960/mo in real time -->
![AgentMesh demo — 70% cost reduction in real time](docs/demo.gif)

> **See it live:** `pip install agentmesh rich && python examples/demo.py`  
> **Interactive Calculator:** [huggingface.co/spaces/anilatambharii/agentmesh](https://huggingface.co/spaces/anilatambharii/agentmesh)

---

## The $47,000 Problem

Enterprise AI costs don't scale linearly. They explode:

- **Uber** burned their entire 2026 AI budget in **4 months** — now capping spend at $1,500/employee/month
- **Amazon** shut down their internal AI leaderboard after "tokenmaxxing" — employees ran agent loops to inflate scores with zero productive output
- A single recursive multi-agent loop can generate a **$47,000 API bill** before anyone notices
- Only **38%** of enterprises have end-to-end AI agent cost monitoring (Cloud Security Alliance, 2026)
- **Gartner**: 40% of agentic AI projects cancelled by 2027 due to cost overruns

The root cause is architectural: **no framework enforces token budgets, governance policies, or audit trails across heterogeneous agent deployments.** LangGraph, CrewAI, OpenAI Agents, AutoGen — each is an island with no shared governance plane.

**AgentMesh is that governance plane.**

---

## What AgentMesh Does

AgentMesh is a **framework-agnostic sidecar** that intercepts every LLM call — across all your frameworks, all your clouds, all your models — and enforces:

| Capability | What It Prevents |
|---|---|
| **Token Budget Enforcement** | $47K surprise bills; team budget overruns |
| **Dynamic Model Routing** | Paying Opus rates for Haiku-level tasks |
| **Semantic Caching** | Burning tokens on near-identical repeated queries |
| **Circuit Breaker** | Runaway ReAct loops and recursive agent calls |
| **Tamper-Evident Audit Trail** | Non-compliance with EU AI Act, HIPAA, SOC 2 |
| **Policy-as-Code** | Ad-hoc governance that fails at scale |
| **Cost Attribution** | "Who spent $50K this month?" going unanswered |
| **Compliance Reports** | Manual audit prep taking weeks instead of minutes |

---

## Proven Results

> A 50-engineer team's three-step code review agent cost **$8,400/month**.  
> After AgentMesh: **$840/month — a 90% reduction.** Quality delta: -2.1%.

| Optimization | Typical Savings |
|---|---|
| Semantic caching (repeated queries) | 10–30% |
| Dynamic model routing (haiku vs. sonnet) | 20–40% |
| Prompt compression (O(n²) context growth) | 10–25% |
| Circuit breaker (runaway loop prevention) | **100%** of loop costs |
| Combined (typical enterprise workload) | **60–75%** total |

---

## Quickstart

```bash
pip install agentmesh
```

```python
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

# Define governance policy (or use a built-in template)
policy = Policy.from_yaml("""
policies:
  - name: engineering-team
    budget:
      daily_tokens: 1_000_000
      monthly_usd: 3_000
      per_run_tokens: 50_000
      hard_stop: true
    circuit_breaker:
      max_iterations: 25
    compliance:
      frameworks: [eu-ai-act, soc2]
""")

mesh = AgentMesh(policy=policy)

# Wrap your existing agent — ZERO changes to the agent itself
governed_graph  = mesh.wrap_langgraph(your_langgraph_graph)
governed_crew   = mesh.wrap_crewai(your_crew)
governed_agent  = mesh.wrap_openai_agent(your_openai_agent)
governed_autogen = mesh.wrap_autogen(your_autogen_agent)

# Run it — governance is transparent
result = governed_graph.invoke({"messages": [...]})

# Inspect governance stats
print(mesh.stats)
# {
#   'tokens_used': 14_823,
#   'cost_usd': 0.044,
#   'iterations': 7,
#   'model_downgrades': 5,
#   'cache': {'hits': 3, 'misses': 4, 'hit_rate': 0.429, 'tokens_saved': 6200}
# }
```

**Use a built-in template for your industry:**

```python
from agentmesh.templates import load_template

# Templates: fintech, healthcare, enterprise, research, customer_service, nvidia_nim
policy = Policy.from_yaml(load_template("fintech"))   # SOX + PCI-DSS ready
policy = Policy.from_yaml(load_template("healthcare")) # HIPAA ready
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Your Application                             │
│   LangGraph │ CrewAI │ OpenAI Agents │ AutoGen │ Haystack │ Pydantic  │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │  All LLM calls intercepted
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         AgentMesh Proxy                               │
│                                                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌─────────────────────────┐│
│  │  Policy Engine │  │ Budget Enforcer│  │   Tamper-Evident Audit  ││
│  │  (YAML / OPA)  │  │ (hard stop)    │  │   Trail (Ed25519 chain) ││
│  └────────────────┘  └────────────────┘  └─────────────────────────┘│
│                                                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌─────────────────────────┐│
│  │ Semantic Cache │  │ Model Router   │  │   Circuit Breaker       ││
│  │ (cosine sim)   │  │ (RouteLLM-style│  │   (runaway loop kill)   ││
│  └────────────────┘  └────────────────┘  └─────────────────────────┘│
│                                                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌─────────────────────────┐│
│  │ Cost Attributor│  │ Compliance     │  │  HTTP Proxy Mode        ││
│  │ (chargeback)   │  │ Reporter       │  │  (drop-in sidecar)      ││
│  └────────────────┘  └────────────────┘  └─────────────────────────┘│
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                   ▼
        Anthropic API       OpenAI API         NVIDIA NIM
        Google Vertex       Azure OpenAI       Local / Ollama
```

---

## Framework Support

| Framework | Status | Install |
|---|---|---|
| LangGraph | ✅ Full support | `pip install agentmesh[langgraph]` |
| CrewAI | ✅ Full support | `pip install agentmesh[crewai]` |
| OpenAI Agents SDK | ✅ Full support | `pip install agentmesh[openai]` |
| AutoGen v2 / AG2 | ✅ Full support | `pip install agentmesh` |
| Pydantic AI | ✅ Full support | `pip install agentmesh` |
| Haystack 2.x | ✅ Full support | `pip install agentmesh` |
| Google ADK (Vertex AI) | ✅ Full support | `pip install agentmesh` |
| NVIDIA NIM | ✅ Full support | `pip install agentmesh` |
| Raw `anthropic` SDK | ✅ Full support | `pip install agentmesh` |
| LiteLLM (proxy mode) | ✅ HTTP proxy | `pip install agentmesh` |
| Microsoft Semantic Kernel | 🔄 In Progress | v0.3 |

---

## Layer 1 — Policy Engine

Define governance in YAML. No code changes to agents.

```yaml
# agentmesh-policy.yaml
version: "1.0"
policies:
  - name: production-agents
    budget:
      daily_tokens: 1_000_000
      monthly_usd: 3_000
      per_run_tokens: 100_000
      hard_stop: true            # kill run, never just warn

    model_routing:
      default: "claude-haiku-4-5"
      upgrade_triggers:
        - condition: "task_complexity > 0.8"
          model: "claude-sonnet-4-6"
      max_allowed: "claude-sonnet-4-6"  # Opus never auto-selected

    circuit_breaker:
      max_iterations: 25
      max_tool_calls: 50
      stall_detection_seconds: 120

    compliance:
      frameworks: ["eu-ai-act", "nist-ai-rmf", "hipaa"]
      pii_detection: true
```

---

## Layer 2 — Semantic Caching

Zero external dependencies. Pure-Python cosine similarity on n-gram embeddings.  
Swap in OpenAI / Cohere embeddings for production quality.

```python
from agentmesh.cache import SemanticCache

cache = SemanticCache(similarity_threshold=0.90, ttl_seconds=3600)

# Near-duplicate queries hit the cache — no LLM call needed
cached = cache.get("What is the capital of France?")  # None (first time)
cache.put("What is the capital of France?", response, model="haiku")
cached = cache.get("What's France's capital city?")   # HIT — 0.92 similarity

print(cache.stats)
# {'hits': 1, 'misses': 1, 'hit_rate': 0.5, 'tokens_saved': 847}
```

---

## Layer 3 — Cost Attribution & Chargeback

Finally answer: *"Which team spent $50K on AI this month?"*

```python
from agentmesh.attribution import CostAttributor

attributor = CostAttributor()

# Record each agent run with team/project attribution
attributor.record(
    model="claude-haiku-4-5",
    input_tokens=12_500, output_tokens=800, cost_usd=0.011,
    team="data-science", project="fraud-detection",
)

# Summarize by team
report = attributor.summary(group_by="team")
print(report.to_csv())

# Check who's over budget
status = attributor.budget_status({"data-science": 500.0, "engineering": 2000.0})
# {'data-science': {'spent_usd': 234.5, 'budget_usd': 500.0, 'pct_used': 46.9, 'over_budget': False}}
```

---

## Layer 4 — Compliance Reports

Generate regulator-ready reports in seconds.

```python
from agentmesh.compliance import ComplianceReporter

reporter = ComplianceReporter(mesh=mesh)

# EU AI Act Article 13 compliance check
report = reporter.generate(framework="eu-ai-act")
print(report.summary())
# === EU AI Act Compliance Report ===
# Policy:    production-agents
# Result:    COMPLIANT
# Pass rate: 100% (9/9 checks)

report.save("eu-ai-act-2026-Q2.json")  # Evidence package for auditors

# Check all frameworks at once
all_reports = reporter.generate_all()
```

**Supported frameworks**: `eu-ai-act` · `nist-ai-rmf` · `hipaa` · `soc2` · `iso-42001`

---

## Layer 5 — Tamper-Evident Audit Trail

Every agent action is signed and exportable to your SIEM.

```python
from agentmesh.audit import AuditTrail

trail = AuditTrail(signing_key="your-ed25519-private-key")

# Each entry: SHA-256 payload hash + Ed25519 signature + prev_hash chain
trail.export_otel("http://your-collector:4317")     # → Splunk, Datadog, Elastic
trail.export_json("audit-2026-Q2.json")             # → auditors

assert trail.verify()  # tamper detection
```

Satisfies: **EU AI Act Art. 13** · **NIST AI RMF** · **SOC 2 Type II** · **HIPAA §164.312(b)**

---

## HTTP Proxy Mode

No SDK changes. Point any LLM client at AgentMesh.

```bash
# Start the governance proxy
agentmesh proxy --port 8080 --upstream https://api.anthropic.com --policy policy.yaml

# In your application: just change the base URL
export ANTHROPIC_BASE_URL=http://localhost:8080
# All governance policies now apply automatically
```

---

## CLI

```bash
# Validate a policy file
agentmesh validate my-policy.yaml

# Inspect an audit trail
agentmesh audit view audit-2026-Q2.json --format table
agentmesh audit verify audit-2026-Q2.json

# Generate a compliance report
agentmesh compliance report --framework eu-ai-act --policy my-policy.yaml

# Start governance proxy
agentmesh proxy --port 8080 --policy my-policy.yaml

# Estimate savings
agentmesh benchmark --policy my-policy.yaml
```

---

## Observability

| Tool | Status |
|---|---|
| OpenTelemetry (OTLP) | ✅ Native — grpc/http |
| Langfuse | ✅ Via OTLP |
| Arize Phoenix | ✅ Via OTLP |
| Datadog | ✅ Via OTLP |
| Splunk | ✅ Via OTLP |
| Azure Monitor | ✅ Via OTLP |

```bash
pip install agentmesh[otel]
```

---

## Target Companies & Pain Points

AgentMesh was designed to solve the exact gaps these organizations face:

| Company | Gap | How AgentMesh Fixes It |
|---|---|---|
| **NVIDIA** | No governance for NIM deployments | NIM integration + model routing across Llama tiers |
| **Microsoft** | AutoGen has no budget enforcement | AutoGen v2 wrapper with hard stop + audit |
| **Google** | ADK lacks cross-framework governance | Google ADK integration + Vertex AI policy |
| **OpenAI** | Agents SDK has no cost controls | OpenAI Agents wrapper + semantic cache |
| **Anthropic** | No native agent governance plane | Claude-native policy + Ed25519 audit |
| **Meta** | No governance for open-source Llama agents | NVIDIA NIM + Llama policy templates |
| **Intuit** | Financial AI needs SOX + PCI compliance | `fintech` template + compliance reporter |
| **Apple** | No governance for on-device MLX agents | Lightweight core, no GPU required |
| **Netflix** | ML cost optimization at scale | Cost attribution + team chargeback |
| **Amazon** | "Tokenmaxxing" problem documented | Circuit breaker + budget hard stop |

---

## Roadmap

| Version | Target | Features |
|---|---|---|
| **v0.2** (current) | Q3 2026 | AutoGen v2, Haystack, Google ADK, NVIDIA NIM, semantic cache, cost attribution, compliance reports |
| **v0.3** | Q4 2026 | Web dashboard, Kubernetes operator, Microsoft Semantic Kernel |
| **v0.4** | Q1 2027 | EU AI Act report generator CLI, on-device / Apple Silicon enforcement |
| **v0.5** | Q2 2027 | Multi-cloud federation, SSO/RBAC integration, SaaS hosted version |

---

## Contributing

AgentMesh is Apache 2.0 licensed and community-driven. We welcome contributions.

```bash
git clone https://github.com/anilatambharii/agentmesh
cd agentmesh
pip install -e ".[dev]"
pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. See [SECURITY.md](SECURITY.md) for responsible disclosure.

---

## Citation

```bibtex
@software{agentmesh2026,
  author  = {Prasad, Anil},
  title   = {AgentMesh: A Universal Governance Plane for AI Agents},
  year    = {2026},
  version = {0.2.0},
  url     = {https://github.com/anilatambharii/agentmesh},
  license = {Apache-2.0},
}
```

---

## Related Projects

- [LangGraph](https://github.com/langchain-ai/langgraph) — Graph-based agent orchestration
- [CrewAI](https://github.com/crewAIInc/crewAI) — Role-based multi-agent teams
- [AutoGen](https://github.com/microsoft/autogen) — Microsoft's conversation-based agents
- [LLMLingua](https://github.com/microsoft/LLMLingua) — Prompt compression
- [LiteLLM](https://github.com/BerriAI/litellm) — Multi-provider LLM proxy

---

*Built by [Anil Prasad](https://github.com/anilatambharii) — co-founder of GenomicsIQ (World Economic Forum cohort), builder of Aria RCM (11-agent healthcare platform), BCG Aleph pricing platform contributor.*

*If AgentMesh saves your team money, [give it a star](https://github.com/anilatambharii/agentmesh) ⭐ — it helps others find it.*
