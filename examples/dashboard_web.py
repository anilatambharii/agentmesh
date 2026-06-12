"""
AgentMesh Web Dashboard — Gradio browser-based governance monitor.

6 tabs showing every AgentMesh governance layer in action:
  1. Live Run      — run a simulated agent, watch governance fire in real time
  2. Budget & Cost — token usage, cost breakdown, model distribution
  3. Semantic Cache — hit/miss analysis, tokens saved, similarity scores
  4. Audit Trail   — tamper-evident log, chain verification
  5. Cost Attribution — chargeback by team/project
  6. Compliance    — EU AI Act, HIPAA, SOC 2, NIST reports

Usage:
    pip install gradio
    python examples/dashboard_web.py
    # then open http://localhost:7860
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import gradio as gr
except ImportError:
    print("Install gradio: pip install gradio")
    sys.exit(1)

from examples.simulation import run_scenario, SCENARIOS, TEMPLATES, SimEvent

# ── State (accumulated across a run) ─────────────────────────────────────────

class RunState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.events:          list[SimEvent] = []
        self.running:         bool = False
        self.final_data:      dict = {}
        self.log_lines:       list[str] = []

    def add(self, e: SimEvent):
        self.events.append(e)
        self.log_lines.append(_event_to_log(e))
        if e.kind == "complete":
            self.final_data = e.data


_state = RunState()


# ── Formatters ────────────────────────────────────────────────────────────────

def _event_to_log(e: SimEvent) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(e.timestamp))
    icons = {
        "step":        "🟢",
        "cache_hit":   "⚡",
        "model_route": "🔀",
        "budget":      "⚠️",
        "circuit":     "🔴",
        "complete":    "✅",
        "error":       "❌",
    }
    icon = icons.get(e.kind, "·")
    if e.kind == "step":
        return f"{ts} {icon} [{e.task_id}] {e.step} | {e.model.split('-')[1] if '-' in e.model else e.model} | {e.tokens_in}+{e.tokens_out} tok | ${e.cost_usd:.4f}"
    elif e.kind == "cache_hit":
        return f"{ts} {icon} [{e.task_id}] {e.step} | CACHE HIT sim={e.cache_similarity:.3f} | saved ~{e.tokens_in+e.tokens_out} tokens"
    elif e.kind == "model_route":
        from_m = e.from_model.split("-")[1] if "-" in e.from_model else e.from_model
        to_m   = e.to_model.split("-")[1]   if "-" in e.to_model   else e.to_model
        return f"{ts} {icon} [{e.task_id}] MODEL ROUTE  {from_m} → {to_m}  (complexity triggered)"
    elif e.kind == "budget":
        return f"{ts} {icon} PROMPT COMPRESSED | {e.message[:80]}"
    elif e.kind == "circuit":
        return f"{ts} {icon} CIRCUIT BREAKER TRIPPED | {e.message}"
    elif e.kind == "complete":
        return f"{ts} {icon} RUN COMPLETE | {e.total_tokens:,} tokens | ${e.total_cost:.4f} | {e.audit_entries} audit entries"
    return f"{ts} · {e.kind}: {e.message[:80]}"


def _stats_md(events: list[SimEvent], final: dict) -> str:
    if not events:
        return "*No run data yet — click **Run Demo Agent** to start.*"
    last = events[-1]
    cache = final.get("cache_stats", {})
    stats = final.get("mesh_stats", {})
    routing = [e for e in events if e.kind == "model_route"]
    hits  = sum(1 for e in events if e.kind == "cache_hit")
    total = sum(1 for e in events if e.kind in ("step", "cache_hit"))

    lines = [
        "| Metric | Value |",
        "|---|---|",
        f"| **Tokens used** | {last.total_tokens:,} |",
        f"| **Cost (USD)** | ${last.total_cost:.4f} |",
        f"| **Iterations** | {last.iteration} |",
        f"| **Audit entries** | {last.audit_entries} |",
        f"| **Cache hit rate** | {hits}/{total}  ({hits/total:.0%})" + " |" if total else "| **Cache hit rate** | — |",
        f"| **Model upgrades** | {len(routing)} |",
        f"| **Budget remaining** | {last.budget_pct:.0%} |",
    ]
    return "\n".join(lines)


# ── Tab 1: Live Run ────────────────────────────────────────────────────────────

def run_agent(scenario: str, template: str, trip: bool):
    """Generator — yields (log_text, stats_md) as the agent runs."""
    _state.reset()
    _state.running = True

    yield (
        "🚀 Starting agent simulation…\n"
        f"   Scenario: {scenario}  |  Template: {template}  |  Circuit-breaker trip: {trip}\n"
        f"   All AgentMesh governance layers active.\n"
        + "─" * 70 + "\n",
        "*Running…*",
    )

    for event in run_scenario(scenario, template, trip_circuit_breaker=trip):
        _state.add(event)
        log = "\n".join(_state.log_lines[-60:])
        stats = _stats_md(_state.events, _state.final_data)
        yield log, stats

    _state.running = False
    log = "\n".join(_state.log_lines)
    stats = _stats_md(_state.events, _state.final_data)
    yield log + "\n" + "─" * 70 + "\n✅ Run complete.", stats


# ── Tab 2: Budget & Cost ──────────────────────────────────────────────────────

def get_budget_data():
    if not _state.events:
        return "*Run an agent first.*", None, None

    step_events = [e for e in _state.events if e.kind in ("step", "cache_hit")]
    if not step_events:
        return "*No step data.*", None, None

    last = _state.events[-1]

    # Model distribution
    model_tally: dict[str, list] = {}
    for e in step_events:
        if e.model:
            key = e.model.split("-")[1] if "-" in e.model else e.model
            if key not in model_tally:
                model_tally[key] = [0, 0.0]
            model_tally[key][0] += e.tokens_in + e.tokens_out
            model_tally[key][1] += e.cost_usd

    # Markdown summary
    md = f"""
### Budget Summary

| | |
|---|---|
| **Tokens used** | {last.total_tokens:,} |
| **Budget remaining** | {last.budget_pct:.0%} |
| **Total cost** | ${last.total_cost:.4f} |
| **Calls made** | {len(step_events)} |
| **Avg cost/call** | ${last.total_cost/len(step_events):.5f} |

### Cost by Model
"""
    for model, (tok, cost) in sorted(model_tally.items(), key=lambda x: -x[1][1]):
        pct = tok / last.total_tokens * 100 if last.total_tokens else 0
        md += f"\n- **{model}**: {tok:,} tokens ({pct:.0f}%)  →  ${cost:.4f}"

    # Cumulative cost chart data
    cum_data = []
    running = 0.0
    for i, e in enumerate(step_events):
        running += e.cost_usd
        cum_data.append([i + 1, round(running, 5)])

    # Model distribution bar data
    dist_data = [[k, v[1]] for k, v in model_tally.items()]

    return md, cum_data, dist_data


# ── Tab 3: Semantic Cache ─────────────────────────────────────────────────────

def get_cache_data():
    if not _state.events:
        return "*Run an agent first.*", []

    hits   = [e for e in _state.events if e.kind == "cache_hit"]
    misses = [e for e in _state.events if e.kind == "step"]
    total  = len(hits) + len(misses)
    hit_r  = len(hits) / total if total else 0

    tokens_saved = sum(e.tokens_in + e.tokens_out for e in hits)
    last_cost    = _state.events[-1].total_cost if _state.events else 0

    md = f"""
### Semantic Cache Performance

| Metric | Value |
|---|---|
| **Hit rate** | {hit_r:.0%}  ({len(hits)} / {total} calls) |
| **Tokens saved** | {tokens_saved:,} |
| **Estimated cost saved** | ~${tokens_saved/1_000_000*3.0:.4f} |
| **Cache entries** | {len(misses)} |

**How it works:** AgentMesh computes n-gram embeddings of each prompt and stores them in memory.
When a new prompt has cosine similarity ≥ 0.88 to a cached entry, the cached response is returned
— no LLM call needed. Zero external dependencies.
"""

    rows = []
    for e in hits:
        rows.append([
            e.task_id, e.step,
            f"{e.cache_similarity:.3f}",
            f"{e.tokens_in + e.tokens_out:,}",
            "✅ HIT",
        ])
    for e in misses:
        rows.append([e.task_id, e.step, "—", f"{e.tokens_in + e.tokens_out:,}", "❌ miss"])

    return md, rows


# ── Tab 4: Audit Trail ────────────────────────────────────────────────────────

def get_audit_data():
    entries = _state.final_data.get("audit_entries", [])
    if not entries:
        return "*Run an agent first.*", []

    rows = [
        [
            e["timestamp"],
            e["event_type"],
            e["agent_id"],
            e["model"],
            str(e["tokens_used"]),
            e["entry_id"],
        ]
        for e in entries
    ]
    md = f"""
### Tamper-Evident Audit Trail

{len(rows)} entries recorded. Each entry contains:
- **SHA-256 payload hash** — proves the prompt/response content
- **Ed25519 signature** — (when signing key configured) proves origin
- **prev_hash** — chains entries together; any tampering breaks the chain

Run `agentmesh audit verify audit.json` to verify chain integrity.
"""
    return md, rows


# ── Tab 5: Cost Attribution ───────────────────────────────────────────────────

def get_attribution_data(group_by: str = "team"):
    key = "attribution" if group_by == "team" else "attribution_by_project"
    data = _state.final_data.get(key, [])

    if not data:
        return "*Run an agent first.*", []

    rows = [
        [
            r["group_key"],
            str(r["call_count"]),
            f"{r['total_tokens']:,}",
            f"${r['total_cost_usd']:.4f}",
            f"${r['avg_cost_per_call']:.5f}",
            ", ".join(r.get("unique_models", [])),
        ]
        for r in data
    ]

    total_cost = sum(r["total_cost_usd"] for r in data)
    md = f"""
### Cost Attribution by {group_by.title()}

Total spend this run: **${total_cost:.4f}**

Use `CostAttributor` to generate chargeback reports for finance teams.
Export as CSV: `attributor.summary(group_by="{group_by}").to_csv()`
"""
    return md, rows


# ── Tab 6: Compliance ─────────────────────────────────────────────────────────

def get_compliance_data(framework: str = "eu-ai-act"):
    c = _state.final_data.get("compliance", {})
    if not c:
        return "*Run an agent first, then select a framework.*"

    checks = c.get("checks", [])
    passed = sum(1 for ch in checks if ch["passed"])
    total  = len(checks)
    rate   = passed / total if total else 0
    result = "✅ **COMPLIANT**" if c.get("overall_compliant") else "❌ **NON-COMPLIANT**"
    gaps   = c.get("gaps", [])

    lines = [
        f"### {c.get('framework_name', framework)} Compliance Report",
        "",
        f"| | |",
        f"|---|---|",
        f"| **Policy** | {c.get('policy_name', '—')} |",
        f"| **Result** | {result} |",
        f"| **Pass rate** | {rate:.0%}  ({passed}/{total} checks) |",
        f"| **Audit entries** | {c.get('audit_entry_count', 0)} |",
        f"| **Chain valid** | {'✅ Yes' if c.get('audit_chain_valid') else '⚠️ Not verified'} |",
        "",
    ]

    if gaps:
        lines.append("#### Gaps to Remediate")
        for g in gaps:
            lines.append(f"- {g}")
        lines.append("")

    lines += [
        "#### Check Details",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for ch in checks:
        status = "✅ Pass" if ch["passed"] else "❌ Fail"
        evidence = ch.get("evidence", "")[:60]
        lines.append(f"| `{ch['check_id']}` | {status} | {evidence} |")

    return "\n".join(lines)


# ── Gradio App ────────────────────────────────────────────────────────────────

CSS = """
.tab-nav button { font-weight: 600; }
.log-box textarea { font-family: monospace; font-size: 12px; }
footer { display: none !important; }
"""

def build_app() -> gr.Blocks:
    with gr.Blocks(title="AgentMesh Dashboard") as app:

        # ── Header ──────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="text-align:center; padding: 16px 0 8px;">
          <h1 style="margin:0">🕸️ AgentMesh Dashboard</h1>
          <p style="margin:4px 0; color:#6b7280">
            The governance plane for AI agents &nbsp;·&nbsp;
            <a href="https://github.com/anilatambharii/agentmesh" target="_blank">GitHub</a> &nbsp;·&nbsp;
            <code>pip install agentmesh</code>
          </p>
        </div>
        """)

        with gr.Tabs():

            # ── Tab 1: Live Run ──────────────────────────────────────────────
            with gr.Tab("▶ Live Run"):
                gr.Markdown("""
Run a simulated enterprise agentic workflow. All AgentMesh governance layers fire for real —
only the LLM call is mocked (so you don't need an API key).

Watch: **budget enforcement** · **semantic cache hits** · **model routing** · **circuit breaker** · **audit trail**
                """)
                with gr.Row():
                    scenario_dd = gr.Dropdown(
                        choices=SCENARIOS,
                        value="code-review",
                        label="Scenario",
                        info="Enterprise workflow to simulate",
                    )
                    template_dd = gr.Dropdown(
                        choices=TEMPLATES,
                        value="enterprise",
                        label="Policy Template",
                        info="Governance policy to enforce",
                    )
                    trip_cb = gr.Checkbox(
                        value=False,
                        label="Trip Circuit Breaker",
                        info="Run until the circuit breaker fires",
                    )
                run_btn = gr.Button("🚀 Run Demo Agent", variant="primary", size="lg")

                with gr.Row():
                    with gr.Column(scale=2):
                        log_box = gr.Textbox(
                            label="Governance Event Log",
                            lines=25,
                            max_lines=25,
                            interactive=False,
                            elem_classes=["log-box"],
                            placeholder="Events will stream here as the agent runs…",
                        )
                    with gr.Column(scale=1):
                        stats_md = gr.Markdown("*Configure and click Run.*", label="Live Stats")

                run_btn.click(
                    fn=run_agent,
                    inputs=[scenario_dd, template_dd, trip_cb],
                    outputs=[log_box, stats_md],
                )

            # ── Tab 2: Budget & Cost ─────────────────────────────────────────
            with gr.Tab("💰 Budget & Cost"):
                refresh_budget_btn = gr.Button("🔄 Refresh", size="sm")
                budget_md = gr.Markdown("*Run an agent first.*")

                with gr.Row():
                    cum_chart = gr.LinePlot(
                        value=None,
                        x="Iteration",
                        y="Cumulative Cost ($)",
                        title="Cumulative Cost Over Time",
                        height=300,
                    )
                    dist_chart = gr.BarPlot(
                        value=None,
                        x="Model",
                        y="Cost ($)",
                        title="Cost by Model",
                        height=300,
                    )

                def _refresh_budget():
                    md, cum, dist = get_budget_data()
                    import pandas as pd
                    cum_df  = pd.DataFrame(cum,  columns=["Iteration", "Cumulative Cost ($)"]) if cum  else None
                    dist_df = pd.DataFrame(dist, columns=["Model", "Cost ($)"])               if dist else None
                    return md, cum_df, dist_df

                refresh_budget_btn.click(
                    fn=_refresh_budget,
                    outputs=[budget_md, cum_chart, dist_chart],
                )

            # ── Tab 3: Semantic Cache ────────────────────────────────────────
            with gr.Tab("⚡ Semantic Cache"):
                refresh_cache_btn = gr.Button("🔄 Refresh", size="sm")
                cache_md = gr.Markdown("*Run an agent first.*")
                cache_table = gr.Dataframe(
                    headers=["Task", "Step", "Similarity", "Tokens", "Result"],
                    label="All LLM Calls (with cache status)",
                    interactive=False,
                )

                refresh_cache_btn.click(
                    fn=get_cache_data,
                    outputs=[cache_md, cache_table],
                )

            # ── Tab 4: Audit Trail ───────────────────────────────────────────
            with gr.Tab("🔒 Audit Trail"):
                refresh_audit_btn = gr.Button("🔄 Refresh", size="sm")
                audit_md = gr.Markdown("*Run an agent first.*")
                audit_table = gr.Dataframe(
                    headers=["Time", "Event", "Agent", "Model", "Tokens", "Entry ID"],
                    label="Tamper-Evident Audit Entries",
                    interactive=False,
                )

                gr.Markdown("""
> **Audit chain**: Every entry is linked via `prev_hash`. Modifying any entry
> invalidates all subsequent hashes — making tampering immediately detectable.
> Run `agentmesh audit verify audit.json` to verify integrity offline.
                """)

                refresh_audit_btn.click(
                    fn=get_audit_data,
                    outputs=[audit_md, audit_table],
                )

            # ── Tab 5: Cost Attribution ──────────────────────────────────────
            with gr.Tab("📊 Cost Attribution"):
                group_by_dd = gr.Dropdown(
                    choices=["team", "project"],
                    value="team",
                    label="Group By",
                )
                refresh_attr_btn = gr.Button("🔄 Refresh", size="sm")
                attr_md = gr.Markdown("*Run an agent first.*")
                attr_table = gr.Dataframe(
                    headers=["Group", "Calls", "Tokens", "Cost (USD)", "Avg/Call", "Models"],
                    label="Spend by Group (sorted by cost desc)",
                    interactive=False,
                )

                gr.Markdown("""
**Cost Attribution** answers the question enterprise CFOs always ask: *"Which team spent $50K on AI last month?"*

```python
from agentmesh.attribution import CostAttributor

attributor = CostAttributor()
attributor.record(model="claude-haiku-4-5", cost_usd=0.011, team="data-science")
report = attributor.summary(group_by="team")
print(report.to_csv())  # → send to finance
```
                """)

                def _refresh_attr(group_by):
                    return get_attribution_data(group_by)

                refresh_attr_btn.click(
                    fn=_refresh_attr,
                    inputs=[group_by_dd],
                    outputs=[attr_md, attr_table],
                )

            # ── Tab 6: Compliance ────────────────────────────────────────────
            with gr.Tab("🏛️ Compliance"):
                fw_dd = gr.Dropdown(
                    choices=["eu-ai-act", "nist-ai-rmf", "hipaa", "soc2", "iso-42001"],
                    value="eu-ai-act",
                    label="Compliance Framework",
                )
                refresh_comp_btn = gr.Button("🔄 Generate Report", size="sm")
                comp_md = gr.Markdown("*Run an agent first, then click Generate Report.*")

                gr.Markdown("""
**Auto-generated compliance evidence** for your AI agent deployments.

```python
from agentmesh.compliance import ComplianceReporter

reporter = ComplianceReporter(mesh=mesh)
report = reporter.generate(framework="eu-ai-act")  # or hipaa, soc2, nist-ai-rmf
report.save("evidence-package-Q2-2026.json")       # → send to auditors
```

**Supported**: EU AI Act · NIST AI RMF · HIPAA § 164.312 · SOC 2 Type II · ISO/IEC 42001
                """)

                def _refresh_comp(fw):
                    return get_compliance_data(fw)

                refresh_comp_btn.click(
                    fn=_refresh_comp,
                    inputs=[fw_dd],
                    outputs=[comp_md],
                )

    return app


def main():
    print("=" * 60)
    print("  AgentMesh Web Dashboard")
    print("  Starting at http://localhost:7860")
    print("=" * 60)
    app = build_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(primary_hue="indigo", neutral_hue="slate"),
        css=CSS,
    )


if __name__ == "__main__":
    main()
