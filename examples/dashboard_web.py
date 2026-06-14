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
        "step":         "🟢",
        "cache_hit":    "⚡",
        "model_route":  "🔀",
        "vendor_route": "🌐",
        "budget":       "⚠️",
        "circuit":      "🔴",
        "complete":     "✅",
        "error":        "❌",
        "quota_warn":   "🟡",
        "escalation":   "🚨",
    }
    icon = icons.get(e.kind, "·")
    if e.kind == "step":
        return f"{ts} {icon} [{e.task_id}] {e.step} | {e.model.split('-')[1] if '-' in e.model else e.model} | {e.tokens_in}+{e.tokens_out} tok | ${e.cost_usd:.4f}"
    elif e.kind == "cache_hit":
        return f"{ts} {icon} [{e.task_id}] {e.step} | CACHE HIT sim={e.cache_similarity:.3f} | saved ~{e.tokens_in+e.tokens_out} tokens"
    elif e.kind == "model_route":
        from_m = e.from_model.split("-")[1] if "-" in e.from_model else e.from_model
        to_m   = e.to_model.split("-")[1]   if "-" in e.to_model   else e.to_model
        return f"{ts} {icon} [{e.task_id}] MODEL ROUTE  {from_m} -> {to_m}  (complexity triggered)"
    elif e.kind == "vendor_route":
        return f"{ts} {icon} [{e.task_id}] VENDOR ROUTE  {e.vendor}/{e.model} | {e.message[:70]}"
    elif e.kind == "quota_warn":
        return f"{ts} {icon} QUOTA WARN | {e.message[:90]}"
    elif e.kind == "escalation":
        return f"{ts} {icon} ESCALATION {e.escalation_id} | {e.message[:80]}"
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

def run_agent(scenario: str, template: str, trip: bool, trip_quota: bool = False,
              vendors: str = "anthropic"):
    """Generator — yields (log_text, stats_md) as the agent runs."""
    _state.reset()
    _state.running = True

    vendor_list = [v.strip() for v in vendors.split(",") if v.strip()]

    yield (
        "🚀 Starting agent simulation…\n"
        f"   Scenario: {scenario}  |  Template: {template}  |  Circuit-breaker trip: {trip}\n"
        f"   Quota enforcement: ON  |  Trip quota: {trip_quota}  |  Vendors: {', '.join(vendor_list)}\n"
        f"   All AgentMesh governance layers active.\n"
        + "─" * 70 + "\n",
        "*Running…*",
    )

    for event in run_scenario(
        scenario, template, trip_circuit_breaker=trip,
        enable_quota=True, trip_quota=trip_quota,
        vendors=vendor_list if len(vendor_list) > 1 else None,
    ):
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


# ── Tab 7: Token Quota ────────────────────────────────────────────────────────

def get_quota_data():
    quota = _state.final_data.get("quota_snapshot", [])
    if not quota:
        return "*Run an agent first (quota is enabled by default).*", []

    rows = []
    for r in quota:
        pct = r["pct_used"]
        bar_filled = int(pct * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        status = "WARN" if pct >= 0.70 else "OK"
        if pct >= 1.0:
            status = "BLOCKED"
        rows.append([
            r["key"],
            f"{r['used']:,}",
            f"{r['limit']:,}",
            f"{r['remaining']:,}",
            f"{pct:.0%}",
            bar,
            status,
        ])

    total_used  = sum(r["used"]  for r in quota)
    total_limit = sum(r["limit"] for r in quota)
    util_str = f"{total_used/total_limit:.0%}" if total_limit else "—"
    md = f"""
### Enterprise Token Quota — Current Month

| | |
|---|---|
| **Total used** | {total_used:,} tokens |
| **Total budget** | {total_limit:,} tokens |
| **Overall utilization** | {util_str} |
| **Teams monitored** | {len(quota)} |

Token budgets are enforced **before** any LLM call — no tokens are burned on blocked requests.
When a team's quota is exhausted, AgentMesh auto-files an escalation request.
"""
    return md, rows


# ── Tab 8: Escalations ────────────────────────────────────────────────────────

def get_escalation_data():
    esc = _state.final_data.get("escalations", {})
    if not esc or not esc.get("requests"):
        return "*No escalations yet. Enable 'Trip Quota' in Live Run to see this in action.*", []

    requests = esc.get("requests", [])
    rows = []
    for r in requests:
        rows.append([
            r["id"],
            r["team"],
            r["tool"],
            r["limit_key"],
            r["pct_used"],
            f"{r['requested_tokens']:,}",
            r["priority"],
            r["status"].upper(),
            r["reason"][:60],
            r["created_at"],
        ])

    summary = f"""
### Token Quota Escalation Queue

| | |
|---|---|
| **Total requests** | {esc.get('total', 0)} |
| **Pending approval** | {esc.get('pending', 0)} |
| **Approved** | {esc.get('approved', 0)} |
| **Rejected** | {esc.get('rejected', 0)} |

Escalations are auto-filed when a team's quota is exceeded.
A temporary grant of 50,000 tokens is applied immediately so work can continue while awaiting approval.
Approvers can action requests via: `agentmesh quota approve ESC-0001`
"""
    return summary, rows


# ── Tab 9: Vendor Comparison ──────────────────────────────────────────────────

def get_vendor_data():
    vcomp = _state.final_data.get("vendor_comparison", [])
    if not vcomp:
        return "*Run an agent first.*", []

    rows = []
    for r in vcomp:
        rows.append([
            r["vendor"],
            r["tier"],
            r["model"],
            f"${r['input_per_1m']:.2f}",
            f"${r['output_per_1m']:.2f}",
            f"{r['context_window']:,}",
            f"${r['estimated_cost']:.5f}",
            "CHEAPEST" if r == vcomp[0] else ("RECOMMENDED" if r.get("recommended") else ""),
        ])

    cheapest = vcomp[0]
    most_expensive = vcomp[-1]
    savings_pct = (1 - cheapest["estimated_cost"] / most_expensive["estimated_cost"]) * 100 if most_expensive["estimated_cost"] else 0

    md = f"""
### Multi-Vendor Cost Comparison

*(For 1,000 input tokens + 300 output tokens)*

| | |
|---|---|
| **Cheapest option** | {cheapest['vendor']} / {cheapest['model']} |
| **Best price** | ${cheapest['estimated_cost']:.5f} per call |
| **vs. most expensive** | {savings_pct:.0f}% cheaper |
| **Vendors compared** | {len(set(r['vendor'] for r in vcomp))} |

AgentMesh routes each call to the **cheapest capable model** across all configured vendors.
Enable multi-vendor routing by setting `vendors=["anthropic","openai","google"]` in your policy.
"""
    return md, rows


# ── Gradio App ────────────────────────────────────────────────────────────────

CSS = """
.tab-nav button { font-weight: 600; }
.log-box textarea { font-family: monospace; font-size: 12px; }
footer { display: none !important; }
"""

# JavaScript injected into page <head> via gr.Blocks(js=...) — starts SSE connection
# Gradio 6 wraps this in a function and calls it when the app loads.
LIVE_STREAM_JS = """
() => {
  var STREAM_URL = 'http://localhost:7861/stream?last_n=80';
  var colors = {
    llm_call:'#58a6ff', cache_hit:'#3fb950', cache_miss:'#8b949e',
    quota_warn:'#d29922', quota_block:'#f85149', escalation:'#bc8cff',
    vendor_route:'#39d353', circuit_breaker:'#f78166', budget_warn:'#d29922', compress:'#79c0ff'
  };
  var counts = {llm_call:0, cache_hit:0, cache_miss:0, quota_warn:0, quota_block:0, escalation:0, vendor_route:0};
  var totalTokens = 0, totalCost = 0.0;

  function gid(id) { return document.getElementById(id); }

  function updateHitRate() {
    var calls = counts.cache_hit + counts.cache_miss;
    if (!calls) return;
    var rate = counts.cache_hit / calls;
    var hr = gid('am-hitrate'); if (hr) hr.textContent = (rate*100).toFixed(1)+'%';
    var hb = gid('am-hitbar');  if (hb) hb.style.width = (rate*100)+'%';
  }

  function addRow(ev) {
    var evDiv = gid('am-events'); if (!evDiv) return;
    var color = colors[ev.kind] || '#8b949e';
    var teamStr  = ev.team   ? '<span style="color:#e3b341">'  + ev.team   + '</span>' : '';
    var toolStr  = ev.tool   ? '<span style="color:#79c0ff">'  + ev.tool   + '</span>' : '';
    var modelStr = ev.model  ? '<span style="color:#7ee787">'  + ev.model  + '</span>' : '';
    var vendorStr= ev.vendor ? '<span style="color:#f78166">'  + ev.vendor + '</span>' : '';
    var tokStr   = ev.tokens_used > 0 ? '<span style="color:#8b949e"> '+ev.tokens_used+'tok</span>' : '';
    var pctStr   = ev.quota_pct  > 0 ? '<span style="color:#d29922"> '+(ev.quota_pct*100).toFixed(0)+'%</span>' : '';
    var msgStr   = ev.message ? '<span style="color:#555;font-size:11px"> '+ev.message.substring(0,70)+'</span>' : '';
    var row = document.createElement('div');
    row.style.cssText = 'padding:3px 8px;border-left:3px solid '+color+';margin:2px 0;font-size:11.5px;line-height:1.5;';
    row.innerHTML = '<span style="color:#444">'+(ev.timestamp_iso||'')+'</span> <span style="color:'+color+';font-weight:bold">['+ev.kind+']</span> '+teamStr+' '+toolStr+' '+modelStr+' '+vendorStr+tokStr+pctStr+msgStr;
    evDiv.insertBefore(row, evDiv.firstChild);
    while (evDiv.children.length > 200) { evDiv.removeChild(evDiv.lastChild); }
  }

  function connect() {
    var es = new EventSource(STREAM_URL);
    var statusEl = gid('am-status');

    es.onopen = function() {
      if (statusEl) { statusEl.innerHTML = '&#128994; <b>Live</b> &nbsp;&#8212;&nbsp; AgentMesh event stream connected'; statusEl.style.color='#3fb950'; }
      var evDiv = gid('am-events');
      if (evDiv && evDiv.children.length === 1) { evDiv.innerHTML = ''; }
    };

    es.onmessage = function(e) {
      try {
        var ev = JSON.parse(e.data);
        if (counts.hasOwnProperty(ev.kind)) { counts[ev.kind]++; }
        var idMap = {llm_call:'cnt-calls',cache_hit:'cnt-hits',cache_miss:'cnt-misses',quota_warn:'cnt-warns',quota_block:'cnt-blocks',escalation:'cnt-esc'};
        var elemId = idMap[ev.kind];
        if (elemId) { var el = gid(elemId); if (el) el.textContent = counts[ev.kind]; }
        if (ev.kind === 'vendor_route') { var ve = gid('am-vendors'); if (ve) ve.textContent = counts.vendor_route; }
        if (ev.tokens_used > 0) { totalTokens += ev.tokens_used; var te = gid('am-tokens'); if(te) te.textContent = totalTokens.toLocaleString(); }
        if (ev.cost_usd    > 0) { totalCost   += ev.cost_usd;    var ce = gid('am-cost');   if(ce) ce.textContent = '$'+totalCost.toFixed(6); }
        updateHitRate();
        addRow(ev);
      } catch(err) {}
    };

    es.onerror = function() {
      if (statusEl) { statusEl.innerHTML = '&#128308; <b>Disconnected</b> &mdash; retrying...'; statusEl.style.color='#f85149'; }
      es.close(); setTimeout(connect, 3000);
    };
  }

  function tryInit() {
    if (gid('am-status') && gid('am-events')) { connect(); }
    else { setTimeout(tryInit, 500); }
  }

  setTimeout(tryInit, 800);
}
"""


def build_app() -> gr.Blocks:
    with gr.Blocks(title="AgentMesh Dashboard", js=LIVE_STREAM_JS) as app:

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
                with gr.Row():
                    trip_cb = gr.Checkbox(
                        value=False,
                        label="Trip Circuit Breaker",
                        info="Run until the circuit breaker fires",
                    )
                    trip_quota_cb = gr.Checkbox(
                        value=False,
                        label="Trip Quota (Trigger Escalation)",
                        info="Pre-seed payments team at 100% quota — triggers auto-escalation",
                    )
                    vendors_dd = gr.Dropdown(
                        choices=["anthropic", "anthropic,openai", "anthropic,openai,google"],
                        value="anthropic",
                        label="Vendors",
                        info="Multi-vendor routing (comma-separated)",
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
                    inputs=[scenario_dd, template_dd, trip_cb, trip_quota_cb, vendors_dd],
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

            # ── Tab 7: Token Quota ───────────────────────────────────────────
            with gr.Tab("🔐 Token Quota"):
                refresh_quota_btn = gr.Button("🔄 Refresh", size="sm")
                quota_summary_md = gr.Markdown("*Run an agent first.*")
                quota_table = gr.Dataframe(
                    headers=["Team / User", "Used", "Limit", "Remaining", "Used %", "Usage Bar", "Status"],
                    label="Token Quota by Team / User / Tool",
                    interactive=False,
                )

                gr.Markdown("""
**Enterprise Token Governance** — every AI interaction (VS Code Copilot, Teams bots,
GitHub CI, Excel AI) flows through a single quota layer.

```python
from agentmesh.quota import QuotaPolicy, QuotaEnforcer

policy = QuotaPolicy(
    team_monthly_tokens={"engineering": 1_000_000, "payments": 500_000},
    warn_at_pct=0.70,       # warn at 70%
    hard_stop_at_pct=1.00,  # block at 100%
    auto_escalate=True,     # auto-file ticket when blocked
)
enforcer = QuotaEnforcer(policy)
enforcer.check(identity)   # → PASS / WARN / BLOCK
```

**Integrates with**: Jira · Slack · Email · ServiceNow — auto-files escalation tickets.
                """)

                refresh_quota_btn.click(
                    fn=get_quota_data,
                    outputs=[quota_summary_md, quota_table],
                )

            # ── Tab 8: Escalations ───────────────────────────────────────────
            with gr.Tab("🚨 Escalations"):
                refresh_esc_btn = gr.Button("🔄 Refresh", size="sm")
                esc_summary_md = gr.Markdown("*Run an agent first (enable Trip Quota to trigger).*")
                esc_table = gr.Dataframe(
                    headers=["ID", "Team", "Tool", "Limit Key", "Quota %", "Requested", "Priority", "Status", "Reason", "Created"],
                    label="Escalation Queue",
                    interactive=False,
                )

                gr.Markdown("""
**Auto-Escalation Workflow** — when a team hits their quota, AgentMesh auto-files
a request and grants a temporary token budget so work continues uninterrupted.

```python
from agentmesh.quota.escalation import EscalationManager

mgr = EscalationManager(enforcer=enforcer, auto_temp_grant=True)
req = mgr.request(
    identity=identity,
    quota_result=result,
    reason="Q4 board report — need 50K extra tokens",
)
# → Slack DM to #ai-governance + Jira ticket ESC-0042 created
# → Temporary 50K token grant applied immediately
# → Approver clicks Approve in Jira → permanent grant applied
```

**Dispatch channels**: Slack · Email · Jira · ServiceNow
                """)

                refresh_esc_btn.click(
                    fn=get_escalation_data,
                    outputs=[esc_summary_md, esc_table],
                )

            # ── Tab 9: Vendor Comparison ─────────────────────────────────────
            with gr.Tab("🌐 Vendor Routing"):
                refresh_vendor_btn = gr.Button("🔄 Refresh", size="sm")
                vendor_summary_md = gr.Markdown("*Run an agent with multiple vendors to see routing decisions.*")
                vendor_table = gr.Dataframe(
                    headers=["Vendor", "Tier", "Model", "Input/1M", "Output/1M", "Context", "Est. Cost", "Label"],
                    label="Multi-Vendor Cost Comparison (1K input + 300 output tokens)",
                    interactive=False,
                )

                gr.Markdown("""
**Automatic vendor arbitrage** — route every request to the cheapest capable model
across Anthropic, OpenAI, Google, Azure, Mistral, and Cohere.

```python
from agentmesh.optimizer.multi_vendor import MultiVendorRouter

router = MultiVendorRouter(
    vendors=["anthropic", "openai", "google"],
    routing_strategy="cheapest_capable",
)
decision = router.route("Summarize this PR diff")
# → vendor="google", model="gemini-2.0-flash", tier="fast"
# → 9x cheaper than claude-haiku for the same quality tier
```

**Strategies**: `cheapest_capable` · `vendor_preference` · `latency_optimized` · `compliance_safe`
                """)

                refresh_vendor_btn.click(
                    fn=get_vendor_data,
                    outputs=[vendor_summary_md, vendor_table],
                )

            # ── Tab 10: Live Stream ──────────────────────────────────────────
            with gr.Tab("🔴 Live Stream"):
                gr.HTML("""
<div style="background:#0d1117;border-radius:10px;padding:20px;font-family:monospace;">

  <!-- Status bar -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
    <div>
      <span id="am-status" style="color:#58a6ff;font-weight:bold;font-size:14px;">
        ⏳ Connecting to AgentMesh event stream...
      </span>
    </div>
    <div style="color:#444;font-size:11px;">localhost:7861/stream</div>
  </div>

  <!-- Live counters -->
  <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:16px;">
    <div style="background:#161b22;border-radius:6px;padding:10px;text-align:center;border:1px solid #21262d;">
      <div id="cnt-calls" style="color:#58a6ff;font-size:22px;font-weight:bold;">0</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">LLM Calls</div>
    </div>
    <div style="background:#161b22;border-radius:6px;padding:10px;text-align:center;border:1px solid #21262d;">
      <div id="cnt-hits" style="color:#3fb950;font-size:22px;font-weight:bold;">0</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">Cache Hits</div>
    </div>
    <div style="background:#161b22;border-radius:6px;padding:10px;text-align:center;border:1px solid #21262d;">
      <div id="cnt-misses" style="color:#8b949e;font-size:22px;font-weight:bold;">0</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">Cache Misses</div>
    </div>
    <div style="background:#161b22;border-radius:6px;padding:10px;text-align:center;border:1px solid #21262d;">
      <div id="cnt-warns" style="color:#d29922;font-size:22px;font-weight:bold;">0</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">Quota Warns</div>
    </div>
    <div style="background:#161b22;border-radius:6px;padding:10px;text-align:center;border:1px solid #21262d;">
      <div id="cnt-blocks" style="color:#f85149;font-size:22px;font-weight:bold;">0</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">Quota Blocks</div>
    </div>
    <div style="background:#161b22;border-radius:6px;padding:10px;text-align:center;border:1px solid #21262d;">
      <div id="cnt-esc" style="color:#bc8cff;font-size:22px;font-weight:bold;">0</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">Escalations</div>
    </div>
  </div>

  <!-- Hit-rate bar -->
  <div style="margin-bottom:14px;">
    <div style="display:flex;justify-content:space-between;color:#8b949e;font-size:11px;margin-bottom:4px;">
      <span>Cache hit rate</span>
      <span id="am-hitrate">—</span>
    </div>
    <div style="background:#21262d;border-radius:4px;height:6px;overflow:hidden;">
      <div id="am-hitbar" style="background:#3fb950;height:100%;width:0%;transition:width 0.5s;"></div>
    </div>
  </div>

  <!-- Token counter -->
  <div style="display:flex;gap:20px;margin-bottom:16px;color:#8b949e;font-size:12px;">
    <span>Total tokens: <b id="am-tokens" style="color:#e6edf3;">0</b></span>
    <span>Est. cost: <b id="am-cost" style="color:#e6edf3;">$0.000000</b></span>
    <span>Vendors routed: <b id="am-vendors" style="color:#39d353;">0</b></span>
  </div>

  <!-- Event log -->
  <div style="margin-bottom:8px;color:#8b949e;font-size:11px;display:flex;justify-content:space-between;">
    <span>GOVERNANCE EVENT LOG</span>
    <span style="cursor:pointer;color:#58a6ff;" onclick="document.getElementById('am-events').innerHTML=''">[ clear ]</span>
  </div>
  <div id="am-events"
       style="height:380px;overflow-y:auto;background:#010409;border-radius:6px;
              padding:10px;border:1px solid #21262d;">
    <div style="color:#444;font-size:12px;text-align:center;padding:40px 0;">
      Open the <b style="color:#58a6ff">Live Run</b> tab and click <b style="color:#3fb950">Run Demo Agent</b> — events appear here in real time.
    </div>
  </div>

  <!-- Legend -->
  <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#8b949e;">
    <span><span style="color:#58a6ff">■</span> llm_call</span>
    <span><span style="color:#3fb950">■</span> cache_hit</span>
    <span><span style="color:#8b949e">■</span> cache_miss</span>
    <span><span style="color:#d29922">■</span> quota_warn</span>
    <span><span style="color:#f85149">■</span> quota_block</span>
    <span><span style="color:#bc8cff">■</span> escalation</span>
    <span><span style="color:#39d353">■</span> vendor_route</span>
    <span><span style="color:#f78166">■</span> circuit_breaker</span>
  </div>
</div>
""")

    return app


OBS_SERVER_PORT = 7861
_obs_started    = False


def main():
    global _obs_started
    print("=" * 60)
    print("  AgentMesh Web Dashboard")
    print("  Dashboard  : http://localhost:7860")
    print(f"  SSE stream : http://localhost:{OBS_SERVER_PORT}/stream")
    print(f"  REST API   : http://localhost:{OBS_SERVER_PORT}/docs")
    print("=" * 60)

    # Start the real-time observability server (daemon thread — dies with main process)
    if not _obs_started:
        try:
            from agentmesh.server import start_server
            start_server(port=OBS_SERVER_PORT)
            _obs_started = True
            print(f"  [OK] Observability server started on :{OBS_SERVER_PORT}")
        except Exception as exc:
            print(f"  [WARN] Could not start observability server: {exc}")

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
