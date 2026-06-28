"""
AgentMesh HuggingFace Space — Interactive AI Agent Cost Savings Calculator.

Deployed at: https://huggingface.co/spaces/anilatambharii/agentmesh

Run locally:
    pip install gradio
    python spaces/app.py
"""

import gradio as gr

# ── Cost model ──────────────────────────────────────────────────────────────

MODEL_COSTS = {
    "claude-haiku-4-5": 0.80,
    "claude-sonnet-4-6": 3.00,
    "claude-opus-4-8": 15.00,
    "gpt-4o-mini": 0.15,
    "gpt-4o": 2.50,
    "gemini-1.5-flash": 0.075,
    "gemini-1.5-pro": 1.25,
    "meta/llama-3.1-8b (NIM)": 0.20,
    "meta/llama-3.1-70b (NIM)": 0.99,
}


def calculate_savings(
    monthly_tokens_m: float,
    current_model: str,
    team_size: int,
    avg_iterations: int,
    enable_caching: bool,
    enable_routing: bool,
    enable_compression: bool,
    enable_circuit_breaker: bool,
) -> tuple:
    """Calculate estimated cost savings with AgentMesh."""
    cost_per_1m = MODEL_COSTS.get(current_model, 3.0)
    monthly_tokens = monthly_tokens_m * 1_000_000

    # Baseline cost
    baseline_cost = (monthly_tokens / 1_000_000) * cost_per_1m

    # Calculate savings from each feature
    savings_breakdown = {}
    remaining_tokens = monthly_tokens

    if enable_caching:
        cache_savings_pct = 0.20  # 20% of calls are near-duplicates
        saved_tokens = remaining_tokens * cache_savings_pct
        savings_breakdown["Semantic Caching"] = (saved_tokens / 1_000_000) * cost_per_1m
        remaining_tokens -= saved_tokens

    if enable_routing:
        # Route ~70% of calls to haiku, 30% to chosen model
        haiku_cost = MODEL_COSTS["claude-haiku-4-5"]
        blended_cost = haiku_cost * 0.70 + cost_per_1m * 0.30
        routing_savings_per_1m = cost_per_1m - blended_cost
        savings_breakdown["Dynamic Model Routing"] = (remaining_tokens / 1_000_000) * routing_savings_per_1m
        remaining_tokens = remaining_tokens  # tokens same, cost drops

    if enable_compression:
        # O(n²) context growth: compression saves ~30% of tokens in long chains
        compression_pct = min(0.30, 0.05 * avg_iterations)
        saved_tokens = remaining_tokens * compression_pct
        savings_breakdown["Prompt Compression"] = (saved_tokens / 1_000_000) * cost_per_1m
        remaining_tokens -= saved_tokens

    if enable_circuit_breaker:
        # ~5% of runs hit runaway loops; circuit breaker prevents 100% of that waste
        runaway_pct = 0.05
        saved_tokens = remaining_tokens * runaway_pct
        savings_breakdown["Circuit Breaker"] = (saved_tokens / 1_000_000) * cost_per_1m

    total_savings = sum(savings_breakdown.values())
    new_cost = max(baseline_cost - total_savings, baseline_cost * 0.10)
    actual_savings = baseline_cost - new_cost
    savings_pct = (actual_savings / baseline_cost * 100) if baseline_cost > 0 else 0

    # Per-team estimate
    per_team_baseline = baseline_cost / team_size
    per_team_new = new_cost / team_size

    # Breakdown text
    breakdown_lines = ["**Savings Breakdown:**\n"]
    for feature, saving in savings_breakdown.items():
        pct = saving / baseline_cost * 100
        breakdown_lines.append(f"- {feature}: **${saving:,.0f}/mo** ({pct:.0f}% reduction)")

    breakdown_text = "\n".join(breakdown_lines)

    summary = f"""
## 💰 AgentMesh Cost Savings Analysis

| | Without AgentMesh | With AgentMesh |
|---|---|---|
| **Monthly Cost** | **${baseline_cost:,.0f}** | **${new_cost:,.0f}** |
| **Per-Engineer** | ${per_team_baseline:,.0f}/mo | ${per_team_new:,.0f}/mo |
| **Annual Cost** | ${baseline_cost * 12:,.0f} | ${new_cost * 12:,.0f} |
| **Annual Savings** | — | **${actual_savings * 12:,.0f}** |

### Total Savings: {savings_pct:.0f}% (${actual_savings:,.0f}/month)

{breakdown_text}

---

*Based on {monthly_tokens_m:.1f}M tokens/month, {team_size} engineers, {avg_iterations} avg iterations/run.*
    """.strip()

    chart_data = {
        "labels": list(savings_breakdown.keys()),
        "values": [round(v, 2) for v in savings_breakdown.values()],
    }

    return summary, f"${actual_savings:,.0f}/month saved ({savings_pct:.0f}% reduction)"


# ── Gradio UI ─────────────────────────────────────────────────────────────

with gr.Blocks(title="AgentMesh — AI Agent Cost Calculator") as demo:
    gr.HTML("""
    <div class="header">
        <h1>🕸️ AgentMesh Cost Savings Calculator</h1>
        <p><b>The governance plane for AI agents.</b> See how much you'd save.</p>
        <p>
            <a href="https://github.com/anilatambharii/agentmesh" target="_blank">GitHub</a> ·
            <a href="https://pypi.org/project/agentmesh-proxy/" target="_blank">PyPI</a> ·
            <code>pip install agentmesh-proxy</code>
        </p>
    </div>
    """)

    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### Your Current Setup")

            monthly_tokens = gr.Slider(
                minimum=0.1, maximum=500, step=0.1, value=10.0,
                label="Monthly Token Usage (millions)",
                info="Total input + output tokens per month across all agents",
            )
            current_model = gr.Dropdown(
                choices=list(MODEL_COSTS.keys()),
                value="claude-sonnet-4-6",
                label="Primary Model",
            )
            team_size = gr.Slider(
                minimum=1, maximum=500, step=1, value=50,
                label="Team Size (engineers)",
                info="Number of engineers using AI agents",
            )
            avg_iterations = gr.Slider(
                minimum=1, maximum=50, step=1, value=10,
                label="Avg Iterations per Agent Run",
                info="Typical ReAct steps per agent invocation",
            )

            gr.Markdown("### AgentMesh Features to Enable")
            enable_caching = gr.Checkbox(value=True, label="Semantic Caching (10–30% savings)")
            enable_routing = gr.Checkbox(value=True, label="Dynamic Model Routing (15–40% savings)")
            enable_compression = gr.Checkbox(value=True, label="Prompt Compression (5–20% savings)")
            enable_circuit_breaker = gr.Checkbox(value=True, label="Circuit Breaker (prevents runaway loops)")

            calc_btn = gr.Button("Calculate Savings", variant="primary", size="lg")

        with gr.Column(scale=1):
            gr.Markdown("### Results")
            savings_headline = gr.Markdown("*Configure your setup and click Calculate.*")
            result_md = gr.Markdown(elem_classes=["result-box"])

    calc_btn.click(
        fn=calculate_savings,
        inputs=[
            monthly_tokens, current_model, team_size, avg_iterations,
            enable_caching, enable_routing, enable_compression, enable_circuit_breaker,
        ],
        outputs=[result_md, savings_headline],
    )

    gr.Markdown("""
---
### How AgentMesh Works

```python
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

mesh = AgentMesh(policy=Policy.from_yaml("policy.yaml"))

# Wrap any framework — zero changes to your existing agent
governed_graph  = mesh.wrap_langgraph(your_graph)      # LangGraph
governed_crew   = mesh.wrap_crewai(your_crew)           # CrewAI
governed_agent  = mesh.wrap_openai_agent(your_agent)    # OpenAI Agents
governed_autogen = mesh.wrap_autogen(your_agent)        # AutoGen v2

print(mesh.stats)
# {'tokens_used': 45231, 'cost_usd': 0.054, 'cache': {'hit_rate': 0.31}}
```

### Quick Start

```bash
pip install agentmesh-proxy
agentmesh validate my-policy.yaml
agentmesh compliance report --framework eu-ai-act --policy my-policy.yaml
```

Built by [Anil Prasad](https://github.com/anilatambharii) · [Apache 2.0 License](https://github.com/anilatambharii/agentmesh/blob/main/LICENSE)
    """)


if __name__ == "__main__":
    demo.launch(
        theme=gr.themes.Soft(primary_hue="indigo"),
        css=".header { text-align: center; padding: 20px 0; } .result-box { font-size: 1.1em; }",
    )
