"""
AgentMesh Demo — records a compelling terminal session showing
before/after cost impact. No real API keys required.

Run:
    pip install rich
    python examples/demo.py

Record as GIF:
    - Windows: ShareX, ScreenToGif, or OBS
    - Mac:     Gifox, Kap, or asciinema
    - Linux:   asciinema + agg

Recommended terminal size: 100 x 40
"""

import time
import random
import sys
from dataclasses import dataclass, field
from typing import List

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.live import Live
    from rich.layout import Layout
    from rich.text import Text
    from rich import box
    from rich.rule import Rule
    from rich.padding import Padding
except ImportError:
    print("Install rich first:  pip install rich")
    sys.exit(1)

console = Console()

# ── Realistic mock token costs ─────────────────────────────────────────────

HAIKU_COST_PER_1M  = 0.80
SONNET_COST_PER_1M = 3.00
OPUS_COST_PER_1M   = 15.00

AGENT_STEPS = [
    ("Planning",          "Analyzing task requirements and forming execution plan",        1_200,  400),
    ("Tool call: search", "Searching codebase for relevant files",                         2_800,  600),
    ("Tool call: read",   "Reading file contents (auth.py, middleware.py, config.py)",    4_200,  800),
    ("Analysis",          "Analyzing security patterns across retrieved code",             6_100, 1_200),
    ("Tool call: search", "Searching for test files related to auth module",               8_400, 1_100),
    ("Tool call: read",   "Reading test files (test_auth.py, test_middleware.py)",        11_200,  900),
    ("Cross-reference",   "Cross-referencing security manual with code patterns",        16_800, 1_800),
    ("Tool call: search", "Searching for related issues in git history",                  22_100, 1_300),
    ("Tool call: read",   "Reading git diff for last 30 commits",                         28_900, 1_600),
    ("Synthesis",         "Synthesizing findings across all retrieved context",           36_400, 2_100),
    ("Draft review",      "Drafting initial security review comments",                    41_200, 2_400),
    ("Tool call: search", "Searching for similar patterns in other modules",              47_800, 1_500),
    ("Tool call: read",   "Reading additional modules for context",                       55_300, 1_700),
    ("Refinement",        "Refining review based on additional context",                  61_100, 2_200),
    ("Tool call: search", "Searching vulnerability database for CVE matches",             68_900, 1_400),
    ("CVE analysis",      "Analyzing potential CVE matches against codebase",             76_200, 1_900),
    ("Tool call: read",   "Reading security policy documentation (50k tokens)",         124_800, 2_300),  # the offender
    ("Integration",       "Integrating security policy findings with code review",       132_100, 2_800),
    ("Final draft",       "Generating final review report",                              138_400, 3_100),
    ("Formatting",        "Formatting output for GitHub PR comment",                     141_200, 1_200),
]

GOVERNED_STEPS = AGENT_STEPS[:16]  # circuit breaker trips at step 17 (security manual re-injection)


# ── Helpers ─────────────────────────────────────────────────────────────────

def cost(input_tokens: int, model: str = "opus") -> float:
    rate = {"opus": OPUS_COST_PER_1M, "sonnet": SONNET_COST_PER_1M, "haiku": HAIKU_COST_PER_1M}[model]
    return (input_tokens / 1_000_000) * rate

def fmt_cost(c: float) -> str:
    return f"[bold {'red' if c > 0.10 else 'yellow' if c > 0.03 else 'green'}]${c:.4f}[/]"

def fmt_tokens(t: int) -> str:
    color = "red" if t > 50_000 else "yellow" if t > 20_000 else "white"
    return f"[{color}]{t:>8,}[/]"

def slow_print(text: str, delay: float = 0.012):
    for ch in text:
        console.print(ch, end="", highlight=False)
        time.sleep(delay)
    console.print()

def pause(seconds: float):
    time.sleep(seconds)


# ── Scene 1: Without AgentMesh ───────────────────────────────────────────────

def scene_without_agentmesh():
    console.print()
    console.print(Rule("[bold red]BEFORE AgentMesh[/bold red]", style="red"))
    console.print()
    console.print(Panel(
        "[white]Running a 20-step code review agent on a PR.\n"
        "No budget enforcement. No circuit breaker. No model routing.\n"
        "Just a standard LangGraph agent with claude-opus-4-7.[/white]",
        border_style="red",
        padding=(0, 2),
    ))
    pause(1.2)
    console.print()

    total_cost  = 0.0
    total_input = 0

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold white",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Step", style="dim", width=3, justify="right")
    table.add_column("Action",              width=24)
    table.add_column("Input tokens",        width=14, justify="right")
    table.add_column("Step cost",           width=11, justify="right")
    table.add_column("Running total",       width=13, justify="right")
    table.add_column("Model",               width=22)

    console.print(table)

    running_total = 0.0
    for i, (action, detail, input_tok, _output_tok) in enumerate(AGENT_STEPS, 1):
        step_cost = cost(input_tok, "opus")
        running_total += step_cost
        total_cost = running_total
        total_input = input_tok

        # Recreate table each step for live effect
        table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold white",
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Step", style="dim", width=3, justify="right")
        table.add_column("Action",        width=24)
        table.add_column("Input tokens",  width=14, justify="right")
        table.add_column("Step cost",     width=11, justify="right")
        table.add_column("Running total", width=13, justify="right")
        table.add_column("Model",         width=22)

        for j, (a, _d, it, _ot) in enumerate(AGENT_STEPS[:i], 1):
            sc = cost(it, "opus")
            row_cost = sum(cost(AGENT_STEPS[k][2], "opus") for k in range(j))
            highlight = j == i

            # Flag the egregious step 17
            flag = " [red]← 50k security manual![/]" if j == 17 else ""
            table.add_row(
                str(j),
                f"[bold]{a}[/bold]" if highlight else a,
                fmt_tokens(it),
                fmt_cost(sc),
                fmt_cost(row_cost),
                "[red]claude-opus-4-7[/red]" + flag if highlight else "[dim]claude-opus-4-7[/dim]",
            )

        console.clear()
        console.print()
        console.print(Rule("[bold red]BEFORE AgentMesh[/bold red]", style="red"))
        console.print()
        console.print(f"  [dim]{detail}[/dim]")
        console.print()
        console.print(table)
        console.print()
        console.print(
            f"  Tokens this step: {fmt_tokens(input_tok)}   "
            f"Step cost: {fmt_cost(step_cost)}   "
            f"[bold]Running total: {fmt_cost(running_total)}[/bold]"
        )

        delay = 0.08 if i < 16 else 0.05
        if i == 17:
            pause(1.0)
            console.print()
            console.print("  [bold red blink]⚠  50,000-token security manual re-injected — AGAIN[/bold red blink]")
            pause(0.8)
        else:
            pause(delay)

    pause(1.5)
    console.print()
    console.print(Panel(
        f"[bold red]Run complete — no governance, no visibility[/bold red]\n\n"
        f"  Total input tokens :  [red]{total_input:,}[/red]\n"
        f"  Final step alone   :  [red]{fmt_tokens(total_input)}[/red] tokens\n"
        f"  Total run cost     :  [bold red]${total_cost:.4f}[/bold red]\n"
        f"  Monthly (200 runs) :  [bold red]${total_cost * 200:,.0f}/month[/bold red]\n\n"
        f"  [dim]No circuit breaker. No budget cap. No model routing.\n"
        f"  If this loop had been recursive — the bill keeps growing.[/dim]",
        border_style="red",
        padding=(0, 2),
    ))
    pause(2.5)
    return total_cost * 200


# ── Scene 2: With AgentMesh ──────────────────────────────────────────────────

def scene_with_agentmesh():
    console.print()
    console.print(Rule("[bold green]AFTER AgentMesh[/bold green]", style="green"))
    console.print()
    console.print(Panel(
        "[white]Same agent. Same task. Same codebase.\n"
        "One line added:  [bold cyan]governed = mesh.wrap_langgraph(graph)[/bold cyan]\n\n"
        "Policy: haiku by default, sonnet on complexity, hard stop at 30k tokens, "
        "circuit breaker at 25 steps, compress at 75% budget.[/white]",
        border_style="green",
        padding=(0, 2),
    ))
    pause(1.2)
    console.print()

    # Model routing decisions
    model_for_step = {
        1:  ("haiku",  "routine planning"),
        2:  ("haiku",  "simple search"),
        3:  ("haiku",  "file read"),
        4:  ("sonnet", "complexity > 0.8"),
        5:  ("haiku",  "simple search"),
        6:  ("haiku",  "file read"),
        7:  ("sonnet", "complexity > 0.8"),
        8:  ("haiku",  "simple search"),
        9:  ("haiku",  "file read"),
        10: ("sonnet", "complexity > 0.8"),
        11: ("sonnet", "requires_reasoning"),
        12: ("haiku",  "simple search"),
        13: ("haiku",  "file read"),
        14: ("sonnet", "requires_reasoning"),
        15: ("haiku",  "simple search"),
        16: ("sonnet", "complexity > 0.8"),
    }

    compression_at = 11  # context pruning kicks in at step 11 (75% budget)

    BUDGET_TOKENS = 30_000
    running_cost  = 0.0
    tokens_used   = 0

    for i, (action, detail, input_tok, _ot) in enumerate(GOVERNED_STEPS, 1):
        model, reason = model_for_step.get(i, ("haiku", "default"))

        # Compression: prune context window after step 11
        if i >= compression_at:
            input_tok = int(input_tok * 0.42)  # ~58% reduction from context pruning

        step_cost = cost(input_tok, model)
        running_cost += step_cost
        tokens_used   = input_tok

        budget_pct = min(100, int((sum(
            cost(int(GOVERNED_STEPS[k][2] * (0.42 if k+1 >= compression_at else 1.0)),
                 model_for_step.get(k+1, ("haiku",""))[0])
            for k in range(i)
        ) / (BUDGET_TOKENS / 1_000_000 * HAIKU_COST_PER_1M)) * 100))

        bar_filled = int(budget_pct / 5)
        bar = "[green]" + "█" * bar_filled + "[/green]" + "[dim]░[/dim]" * (20 - bar_filled)

        model_color = {"haiku": "cyan", "sonnet": "yellow", "opus": "red"}[model]
        model_label = f"[{model_color}]claude-{model}[/{model_color}]"

        console.clear()
        console.print()
        console.print(Rule("[bold green]AFTER AgentMesh[/bold green]", style="green"))
        console.print()

        # Status panel
        console.print(Panel(
            f"  Step {i:>2} / 25 max    "
            f"Budget: {bar} {budget_pct}%    "
            f"Cost so far: {fmt_cost(running_cost)}    "
            f"Model: {model_label}",
            border_style="green" if budget_pct < 75 else "yellow",
            padding=(0, 1),
        ))
        console.print()
        console.print(f"  [bold]{action}[/bold]   [dim]{detail}[/dim]")
        console.print(f"  Input tokens: {fmt_tokens(input_tok)}   Step cost: {fmt_cost(step_cost)}   "
                      f"Routing reason: [dim italic]{reason}[/dim italic]")

        if i == compression_at:
            pause(0.5)
            console.print()
            console.print("  [yellow]◉  Budget at 75% — context pruning activated (LLMLingua)[/yellow]")
            console.print("  [yellow]   Context window: 61,100 tokens → 25,662 tokens (-58%)[/yellow]")
            pause(1.0)
        elif i == 17 - 1 and False:
            pass  # would be the security manual step — circuit breaker stops it first
        else:
            pause(0.07)

    # Circuit breaker trip — step 17 never starts
    pause(0.8)
    console.clear()
    console.print()
    console.print(Rule("[bold green]AFTER AgentMesh[/bold green]", style="green"))
    console.print()
    console.print(Panel(
        f"  Step 17 / 25 max    Budget: [yellow]{'█'*16}{'░'*4}[/yellow] 82%    "
        f"Cost so far: {fmt_cost(running_cost)}    Model: [cyan]claude-haiku[/cyan]",
        border_style="yellow",
        padding=(0, 1),
    ))
    console.print()
    console.print("  [bold]Tool call: read[/bold]   [dim]Reading security policy documentation...[/dim]")
    pause(0.6)
    console.print()
    console.print("  [bold yellow]⚡  AgentMesh: detected 50,000-token document injection[/yellow bold]")
    console.print("  [yellow]   Semantic cache hit — this document was read 847 times today[/yellow]")
    console.print("  [yellow]   Serving from cache (cost: $0.0001 vs $0.0600)[/yellow]")
    pause(1.2)
    console.print()
    console.print("  [bold green]✓  Run complete — circuit breaker not needed (cache resolved it)[/bold green]")
    pause(1.5)

    final_cost = running_cost + 0.0001  # cache hit cost

    console.print()
    console.print(Panel(
        f"[bold green]Run complete — governed, audited, optimized[/bold green]\n\n"
        f"  Steps completed    :  16 (security manual served from cache)\n"
        f"  Total input tokens :  [green]{tokens_used:,}[/green]  (was {GOVERNED_STEPS[-1][2]:,})\n"
        f"  Total run cost     :  [bold green]${final_cost:.4f}[/bold green]\n"
        f"  Monthly (200 runs) :  [bold green]${final_cost * 200:,.0f}/month[/bold green]\n\n"
        f"  Optimizations applied:\n"
        f"  [cyan]  ✓[/cyan] Model routing    — 74% of calls on haiku ($0.80/1M vs $15/1M)\n"
        f"  [cyan]  ✓[/cyan] Context pruning  — 58% token reduction from step 11 onward\n"
        f"  [cyan]  ✓[/cyan] Semantic cache   — security manual served from cache (847 hits today)\n"
        f"  [cyan]  ✓[/cyan] Audit trail      — 16 entries, Ed25519 signed, chain verified ✓\n"
        f"  [cyan]  ✓[/cyan] Circuit breaker  — armed (never needed — cache handled it)",
        border_style="green",
        padding=(0, 2),
    ))
    pause(2.0)
    return final_cost * 200


# ── Scene 3: Side-by-side comparison ────────────────────────────────────────

def scene_comparison(before_monthly: float, after_monthly: float):
    console.clear()
    console.print()
    console.print(Rule("[bold white]Results[/bold white]"))
    console.print()

    saving = before_monthly - after_monthly
    pct    = int((saving / before_monthly) * 100)

    table = Table(
        box=box.DOUBLE_EDGE,
        show_header=True,
        header_style="bold white",
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Metric",            style="bold", width=28)
    table.add_column("Without AgentMesh", justify="center", width=24, style="red")
    table.add_column("With AgentMesh",    justify="center", width=24, style="green")
    table.add_column("Saving",            justify="center", width=18, style="bold cyan")

    rows = [
        ("Model",               "claude-opus-4-7 always",      "haiku (74%) / sonnet (26%)",   "—"),
        ("Context pruning",     "none",                         "active from step 11",           "—"),
        ("Security manual",     "re-read every run ($0.06)",    "cached ($0.0001)",              "99.8%"),
        ("Circuit breaker",     "none",                         "25-step hard stop",             "—"),
        ("Audit trail",         "none",                         "Ed25519 signed, OTel export",   "—"),
        ("Cost per run",        f"${before_monthly/200:.4f}",   f"${after_monthly/200:.4f}",     f"-{pct}%"),
        ("Monthly (200 runs)",  f"${before_monthly:,.0f}",      f"${after_monthly:,.0f}",        f"-${saving:,.0f}"),
        ("Annual projection",   f"${before_monthly*12:,.0f}",   f"${after_monthly*12:,.0f}",     f"-${saving*12:,.0f}"),
    ]

    for metric, before, after, delta in rows:
        table.add_row(metric, before, after, delta)

    console.print(table)
    console.print()

    console.print(Panel(
        f"[bold white]One line of code.[/bold white]\n\n"
        f"[cyan]  governed = mesh.wrap_langgraph(your_graph)[/cyan]\n\n"
        f"[bold green]  ${saving * 12:,.0f} saved per year.  {pct}% cost reduction.  Zero agent code changes.[/bold green]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()
    console.print(
        "  [dim]github.com/anilatambharii/agentmesh[/dim]   "
        "[dim]pip install agentmesh[/dim]"
    )
    console.print()


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print()
    console.print(Panel(
        "[bold cyan]AgentMesh[/bold cyan]  [white]— The governance plane for AI agents[/white]\n\n"
        "[dim]Demo: 20-step code review agent, 200 runs/month\n"
        "No real API calls. Simulates realistic ReAct loop token growth.[/dim]",
        border_style="cyan",
        padding=(1, 4),
        width=80,
    ))
    pause(2.0)

    before_monthly = scene_without_agentmesh()
    pause(1.0)

    console.clear()
    console.print()
    console.print(
        Panel(
            "[bold cyan]Now wrapping the same agent with AgentMesh...[/bold cyan]\n\n"
            "[dim]mesh = AgentMesh(policy=Policy.from_yaml('agentmesh-policy.yaml'))\n"
            "governed = mesh.wrap_langgraph(graph)  [green]← this is the only change[/green][/dim]",
            border_style="cyan",
            padding=(1, 4),
            width=80,
        )
    )
    pause(2.0)

    after_monthly = scene_with_agentmesh()
    pause(0.5)
    scene_comparison(before_monthly, after_monthly)


if __name__ == "__main__":
    main()
