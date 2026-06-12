# Getting Started with AgentMesh

This guide walks you through installing AgentMesh, defining your first policy, and wrapping an existing agent in under 5 minutes.

## Installation

```bash
# Core (no optional dependencies)
pip install agentmesh

# With your framework
pip install agentmesh[langgraph]    # LangGraph + LangChain
pip install agentmesh[crewai]       # CrewAI
pip install agentmesh[openai]       # OpenAI Agents SDK
pip install agentmesh[otel]         # OpenTelemetry export
pip install agentmesh[compression]  # LLMLingua prompt compression
pip install agentmesh[all]          # Everything
```

## Verify Installation

```bash
agentmesh version
# AgentMesh 0.2.0
# The governance plane for AI agents.
```

## Your First Policy

Create `my-policy.yaml`:

```yaml
version: "1.0"
policies:
  - name: my-first-policy
    budget:
      per_run_tokens: 50_000
      monthly_usd: 100
      hard_stop: true
    circuit_breaker:
      max_iterations: 20
```

Validate it:

```bash
agentmesh validate my-policy.yaml
#   ✓ Policy 'my-first-policy' is valid.
#     Budget limits:     50,000 tokens/run, $100/month
#     Circuit breaker:   max_iterations=20
```

## Wrap Your First Agent

### LangGraph

```python
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, MessagesState
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

# Your existing graph — unchanged
llm = ChatAnthropic(model="claude-haiku-4-5")
graph = StateGraph(MessagesState)
# ... add nodes, edges ...
app = graph.compile()

# Wrap it
mesh = AgentMesh(policy=Policy.from_yaml(open("my-policy.yaml").read()))
governed_app = mesh.wrap_langgraph(app)

# Use it — governance is transparent
result = governed_app.invoke({"messages": [{"role": "user", "content": "Hello"}]})
print(mesh.stats)
```

### CrewAI

```python
from crewai import Crew, Agent, Task
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

crew = Crew(
    agents=[Agent(role="Analyst", goal="...", backstory="...")],
    tasks=[Task(description="...", agent=analyst)],
)

mesh = AgentMesh(policy=Policy.from_yaml(open("my-policy.yaml").read()))
governed_crew = mesh.wrap_crewai(crew)
result = governed_crew.kickoff()
```

### OpenAI Agents SDK

```python
from agents import Agent, Runner
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

agent = Agent(name="Helper", instructions="You are a helpful assistant.")

mesh = AgentMesh(policy=Policy.from_yaml(open("my-policy.yaml").read()))
governed = mesh.wrap_openai_agent(agent)
result = Runner.run_sync(governed, "What is 2 + 2?")
```

### AutoGen v2

```python
import autogen
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

assistant = autogen.AssistantAgent("assistant", llm_config=llm_config)

mesh = AgentMesh(policy=Policy.from_yaml(open("my-policy.yaml").read()))
governed = mesh.wrap_autogen(assistant)
governed.initiate_chat(user_proxy, message="Analyze this dataset")
```

## Use a Built-in Template

Don't want to write policy YAML from scratch? Use a template:

```python
from agentmesh.templates import load_template
from agentmesh.policy.engine import Policy
from agentmesh import AgentMesh

# Available: fintech, healthcare, enterprise, research, customer_service, nvidia_nim
policy = Policy.from_yaml(load_template("enterprise"))
mesh = AgentMesh(policy=policy)
```

## Check Your Stats

After running your agent:

```python
print(mesh.stats)
# {
#   'tokens_used': 14_823,
#   'tokens_remaining': 35_177,
#   'cost_usd': 0.0119,
#   'iterations': 7,
#   'tool_calls': 12,
#   'compressions_applied': 0,
#   'model_upgrades': 2,
#   'model_downgrades': 5,
#   'cache': {
#     'hits': 3, 'misses': 4, 'hit_rate': 0.429,
#     'size': 4, 'tokens_saved': 6200
#   }
# }
```

## Next Steps

- [Policy Reference](policy-reference.md) — full YAML schema documentation
- [Compliance Guide](compliance.md) — generating EU AI Act, HIPAA, SOC 2 reports
- [Cookbook](cookbook.md) — recipes for common enterprise use cases
- [Deploying to Kubernetes](deploying-to-kubernetes.md) — cluster-wide governance
