# AgentMesh Cookbook

Common patterns for enterprise AI agent governance.

---

## 1. Prevent a $47K Surprise Bill

The most common disaster: a recursive multi-agent loop runs for 3 days.

```python
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

policy = Policy.from_yaml("""
policies:
  - name: production-safe
    budget:
      per_run_tokens: 100_000
      monthly_usd: 2000
      hard_stop: true              # die, don't just warn
    circuit_breaker:
      max_iterations: 25           # 25 LLM calls max per run
      max_tool_calls: 50
      stall_detection_seconds: 120 # kill if stuck for 2 min
""")

mesh = AgentMesh(policy=policy)
governed = mesh.wrap_langgraph(your_graph)
```

---

## 2. Internal Cost Attribution (Chargeback by Team)

```python
from agentmesh import AgentMesh
from agentmesh.attribution import CostAttributor

mesh = AgentMesh(policy=policy)
attributor = CostAttributor()

# Run agents for different teams
for team, workflow in [("data-science", ds_workflow), ("engineering", eng_workflow)]:
    mesh.reset()
    result = mesh.wrap_langgraph(workflow).invoke(inputs)
    attributor.record_from_mesh_stats(mesh.stats, team=team, project="q2-analysis")

# Monthly chargeback report
report = attributor.summary(group_by="team")
print(report.to_csv())

# Check who's over budget
status = attributor.budget_status({"data-science": 500.0, "engineering": 2000.0})
for team, s in status.items():
    if s["over_budget"]:
        print(f"ALERT: {team} is over budget! Spent ${s['spent_usd']}")
```

---

## 3. EU AI Act Compliance Evidence Package

```python
from agentmesh import AgentMesh
from agentmesh.compliance import ComplianceReporter
from agentmesh.policy.engine import Policy

policy = Policy.from_yaml("""
policies:
  - name: eu-compliant-agents
    budget:
      per_run_tokens: 50_000
      hard_stop: true
    compliance:
      frameworks: [eu-ai-act, nist-ai-rmf]
""")

mesh = AgentMesh(
    policy=policy,
    audit_signing_key="your-ed25519-key-hex",  # for tamper-evident signatures
)

# ... run your agents ...

# Generate compliance evidence
reporter = ComplianceReporter(mesh=mesh)
report = reporter.generate(framework="eu-ai-act")

if report.overall_compliant:
    report.save(f"evidence/eu-ai-act-{today}.json")
    print(report.summary())
else:
    print("GAPS FOUND:")
    for gap in report.gaps:
        print(f"  - {gap}")
```

---

## 4. Semantic Cache for Repeated Queries

Perfect for customer service bots, FAQ agents, or any high-traffic use case.

```python
from agentmesh import AgentMesh
from agentmesh.cache import SemanticCache

# Or use OpenAI embeddings for better similarity:
# from openai import OpenAI
# openai_client = OpenAI()
# def embedder(text):
#     return openai_client.embeddings.create(input=text, model="text-embedding-3-small").data[0].embedding

cache = SemanticCache(
    similarity_threshold=0.90,
    ttl_seconds=1800,
    # embedder=embedder,  # optional: use real embeddings
)

from agentmesh.core import AgentMeshConfig
config = AgentMeshConfig(
    policy=policy,
    enable_caching=True,
    cache_similarity_threshold=0.90,
)
mesh = AgentMesh(config=config)

# After 1,000 calls with 30% near-duplicate queries:
print(f"Cache hit rate: {mesh.cache.hit_rate:.0%}")
print(f"Tokens saved:   {mesh.cache.tokens_saved:,}")
```

---

## 5. NVIDIA NIM — Govern Open-Source Models

```python
from openai import OpenAI
from agentmesh import AgentMesh
from agentmesh.templates import load_template
from agentmesh.policy.engine import Policy
from agentmesh.integrations.nvidia_nim import wrap_openai_client

nim_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="your-nvidia-api-key",
)

policy = Policy.from_yaml(load_template("nvidia_nim"))
mesh = AgentMesh(policy=policy)

# Wrap the NIM client — governance applies to all calls
governed_nim = wrap_openai_client(nim_client, mesh=mesh)

response = governed_nim.chat.completions.create(
    model="meta/llama-3.1-70b-instruct",
    messages=[{"role": "user", "content": "Explain quantum entanglement"}],
    max_tokens=500,
)
print(mesh.stats["cost_usd"])  # how much this cost
```

---

## 6. Multi-Framework Governance (One Policy, All Frameworks)

```python
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

policy = Policy.from_yaml(open("enterprise-policy.yaml").read())
mesh = AgentMesh(policy=policy)

# All these share the SAME budget pool and audit trail
langgraph_app = mesh.wrap_langgraph(your_graph)
crewai_app    = mesh.wrap_crewai(your_crew)
openai_agent  = mesh.wrap_openai_agent(your_openai_agent)
autogen_agent = mesh.wrap_autogen(your_autogen_agent)

# Budget is shared across all frameworks
# If langgraph_app uses 80,000 tokens, crewai_app will trip
# hard_stop after the remaining 20,000 tokens (for a 100K limit)
```

---

## 7. HTTP Proxy Mode (Zero Code Changes)

When you can't modify the agent codebase:

```bash
# Start the governance proxy (terminal 1)
agentmesh proxy --port 8080 --policy policy.yaml --upstream https://api.anthropic.com

# In your application (no code changes needed)
export ANTHROPIC_BASE_URL=http://localhost:8080
python my_agent_app.py
```

---

## 8. Export Audit Trail to Splunk / Datadog

```python
# After running governed agents
mesh.audit.export_otel("http://splunk-collector:4317")     # Splunk HEC
mesh.audit.export_otel("http://datadog-agent:4317")        # Datadog
mesh.audit.export_otel("http://elastic-apm:4317")          # Elastic APM

# Or export to file for archival
mesh.audit.export_json("audit-2026-Q2.json")
```

---

## 9. Async Agent Governance

```python
import asyncio
from agentmesh import AgentMesh

mesh = AgentMesh(policy=policy)

async def run_agent():
    # Use intercept_async for async LLM calls
    result = await mesh.intercept_async(
        async_llm_call,
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Hello"}],
    )
    return result

asyncio.run(run_agent())
```

---

## 10. Dynamic Budget by Time of Day

```python
from agentmesh.policy.engine import Policy
import datetime

def get_policy() -> Policy:
    hour = datetime.datetime.now().hour
    if 9 <= hour <= 17:  # Business hours: premium budget
        budget_str = "monthly_usd: 5000\nper_run_tokens: 200_000"
    else:  # Off-hours: economy budget
        budget_str = "monthly_usd: 500\nper_run_tokens: 20_000"

    return Policy.from_yaml(f"""
policies:
  - name: time-aware-policy
    budget:
      {budget_str}
      hard_stop: true
    model_routing:
      default: "{'claude-sonnet-4-6' if 9 <= hour <= 17 else 'claude-haiku-4-5'}"
""")

mesh = AgentMesh(policy=get_policy())
```
