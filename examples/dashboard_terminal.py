"""
AgentMesh Terminal Dashboard — Rich live governance monitor.

Runs a simulated enterprise agent with all AgentMesh governance layers
and displays real-time metrics in a split-panel terminal layout.

Usage:
    pip install rich
    python examples/dashboard_terminal.py
    python examples/dashboard_terminal.py --scenario customer-service --template fintech
    python examples/dashboard_terminal.py --trip-circuit-breaker
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

logging.disable(logging.INFO)

# ── Path setup so we can import from project root ───────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from rich import box
    from rich.columns import Columns
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn
    from rich.table import Table
    from rich.text import Text
    from rich import print as rprint
except ImportError:
    print("Install rich: pip install rich")
    sys.exit(1)

from examples.simulation import run_scenario, SCENARIOS, TEMPLATES, SimEvent

console = Console()

# ── Colour palette ────────────────────────────────────────────────────────────
COLOR_HEADER   = "bold cyan"
COLOR_OK       = "bold green"
COLOR_WARN     = "bold yellow"
COLOR_ERROR    = "bold red"
COLOR_DIM      = "dim"
COLOR_CACHE    = "bold magenta"
COLOR_ROUTE    = "bold blue"
COLOR_BUDGET   = "bold yellow"
COLOR_CIRCUIT  = "bold red"

MODEL_COLORS = {
    "haiku":  "green",
    "sonnet": "yellow",
    "opus":   "red",
    "mini":   "green",
    "flash":  "green",
}


def _model_color(model: str) -> str:
    for k, c in MODEL_COLORS.items():
        if k in model.lower():
            return c
    return "white"


# ── Layout builders ───────────────────────────────────────────────────────────

def _build_header_panel(scenario: str, template: str) -> Panel:
    t = Text()
    t.append("[AgentMesh]", style="bold cyan")
    t.append("  |  ", style="dim")
    t.append(f"scenario: {scenario}", style="cyan")
    t.append("  |  ", style="dim")
    t.append(f"template: {template}", style="cyan")
    t.append("  |  ", style="dim")
    t.append("All governance layers: ACTIVE", style="bold green")
    return Panel(t, style="cyan", padding=(0, 1))


def _build_stats_panel(events: list[SimEvent]) -> Panel:
    if not events:
        return Panel("[dim]Waiting for agent to start...[/dim]", title="[Stats] Live Stats", border_style="cyan")

    last = events[-1]
    step_events = [e for e in events if e.kind in ("step", "cache_hit")]
    cache_hits  = sum(1 for e in events if e.kind == "cache_hit")
    total_calls = len(step_events)
    routes      = [e for e in events if e.kind == "model_route"]

    budget_pct  = last.budget_pct
    budget_bar  = _make_bar(1.0 - budget_pct, width=20,
                             color="red" if budget_pct < 0.20 else "yellow" if budget_pct < 0.50 else "green")

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", width=22)
    t.add_column()

    t.add_row("Tokens used",    f"[bold]{last.total_tokens:>8,}[/bold]  [dim]{budget_bar}[/dim]  [dim]{budget_pct:.0%} remaining[/dim]")
    t.add_row("Cost (USD)",     f"[bold green]${last.total_cost:.4f}[/bold green]")
    t.add_row("Iterations",     f"[bold]{last.iteration}[/bold]")
    t.add_row("Audit entries",  f"[bold]{last.audit_entries}[/bold]  [dim](tamper-evident chain)[/dim]")
    t.add_row()
    t.add_row("Cache hit rate", f"[bold magenta]{cache_hits}/{total_calls}[/bold magenta]  ({cache_hits/total_calls:.0%})" if total_calls else "-")
    t.add_row("Model routes",   f"[bold blue]{len(routes)}[/bold blue]  [dim](" + ", ".join(f"{e.from_model.split('-')[-1]}->{e.to_model.split('-')[-1]}" for e in routes[-3:]) + ")[/dim]" if routes else "  [dim](none yet)[/dim]")

    return Panel(t, title="[Stats] Live Stats", border_style="cyan", padding=(0, 1))


def _build_event_feed(events: list[SimEvent], max_rows: int = 18) -> Panel:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=True)
    table.add_column(width=8,  style="dim")
    table.add_column(width=12)
    table.add_column(width=14, style="dim")
    table.add_column()

    recent = events[-max_rows:]
    for e in recent:
        ts = time.strftime("%H:%M:%S", time.localtime(e.timestamp))
        if e.kind == "step":
            icon  = "[green]*[/green]"
            label = f"[{_model_color(e.model)}]{e.model.split('-')[1] if '-' in e.model else e.model}[/{_model_color(e.model)}]"
            task  = f"[dim]{e.task_id}[/dim] {e.step}"
            detail = f"[dim]{e.tokens_in:,}+{e.tokens_out:,} tok  ${e.cost_usd:.4f}[/dim]"
        elif e.kind == "cache_hit":
            icon  = "[magenta]#[/magenta]"
            label = "[magenta]CACHE HIT[/magenta]"
            task  = f"[dim]{e.task_id}[/dim] {e.step}"
            detail = f"[dim]similarity={e.cache_similarity:.3f}  saved ~{e.tokens_in+e.tokens_out:,} tokens[/dim]"
        elif e.kind == "model_route":
            icon  = "[blue]->[/blue]"
            label = "[blue]ROUTED[/blue]"
            task  = f"[dim]{e.task_id}[/dim]"
            detail = f"[dim]{e.from_model.split('-')[1] if '-' in e.from_model else e.from_model}[/dim] -> [yellow]{e.to_model.split('-')[1] if '-' in e.to_model else e.to_model}[/yellow]"
        elif e.kind == "budget":
            icon  = "[yellow]![/yellow]"
            label = "[yellow]COMPRESS[/yellow]"
            task  = f"[dim]{e.step}[/dim]"
            detail = f"[dim]{e.message[:60]}[/dim]"
        elif e.kind == "circuit":
            icon  = "[red]X[/red]"
            label = "[red]BREAKER[/red]"
            task  = "[red]TRIPPED[/red]"
            detail = f"[dim]{e.message[:60]}[/dim]"
        elif e.kind == "complete":
            icon  = "[bold green]OK[/bold green]"
            label = "[bold green]DONE[/bold green]"
            task  = ""
            detail = f"[dim]{e.total_tokens:,} tokens  ${e.total_cost:.4f}  {e.audit_entries} audit entries[/dim]"
        else:
            icon, label, task, detail = ".", e.kind, "", e.message[:60]

        table.add_row(ts, icon + " " + label, task, detail)

    return Panel(table, title="[Events] Governance Event Feed", border_style="blue", padding=(0, 0))


def _build_budget_panel(events: list[SimEvent]) -> Panel:
    if not events:
        return Panel("[dim]No data yet[/dim]", title="[$] Budget", border_style="yellow")

    last = events[-1]
    pct_used = 1.0 - last.budget_pct
    bar = _make_bar(pct_used, width=30,
                    color="red" if pct_used > 0.85 else "yellow" if pct_used > 0.60 else "green")

    step_events = [e for e in events if e.kind in ("step", "cache_hit")]
    model_tally: dict[str, tuple[int, float]] = {}
    for e in step_events:
        if e.model:
            prev_tok, prev_cost = model_tally.get(e.model, (0, 0.0))
            model_tally[e.model] = (prev_tok + e.tokens_in + e.tokens_out, prev_cost + e.cost_usd)

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", width=20)
    t.add_column()
    t.add_row("Used", f"{bar}  {pct_used:.0%}")
    t.add_row("Total cost", f"[bold green]${last.total_cost:.4f}[/bold green]")
    t.add_row()
    t.add_row("[dim]By model[/dim]", "")
    for model, (tok, cost) in sorted(model_tally.items(), key=lambda x: -x[1][1]):
        short = model.split("-")[1] if "-" in model else model
        t.add_row(
            f"  [{_model_color(model)}]{short}[/{_model_color(model)}]",
            f"{tok:,} tok  [green]${cost:.4f}[/green]"
        )

    return Panel(t, title="[$] Budget", border_style="yellow", padding=(0, 1))


def _build_cache_panel(events: list[SimEvent]) -> Panel:
    step_events = [e for e in events if e.kind in ("step", "cache_hit")]
    hits   = [e for e in events if e.kind == "cache_hit"]
    misses = [e for e in step_events if e.kind != "cache_hit"]
    total  = len(step_events)
    hit_r  = len(hits) / total if total else 0

    tokens_saved = sum(e.tokens_in + e.tokens_out for e in hits)
    cost_saved   = sum(e.cost_usd for e in hits)

    hit_bar = _make_bar(hit_r, width=20, color="magenta")

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", width=16)
    t.add_column()
    t.add_row("Hit rate", f"{hit_bar}  [bold magenta]{hit_r:.0%}[/bold magenta]  ({len(hits)}/{total})")
    t.add_row("Tokens saved", f"[bold]{tokens_saved:,}[/bold]")
    t.add_row("Cost saved",   f"[bold green]${cost_saved:.4f}[/bold green]")

    if hits:
        t.add_row()
        t.add_row("[dim]Recent hits[/dim]", "")
        for e in hits[-3:]:
            t.add_row(f"  {e.task_id}", f"[dim]{e.step}  sim={e.cache_similarity:.3f}[/dim]")

    return Panel(t, title="[Cache] Semantic Cache", border_style="magenta", padding=(0, 1))


def _make_bar(fraction: float, width: int = 20, color: str = "green") -> str:
    filled = int(fraction * width)
    empty  = width - filled
    return f"[{color}]{'#' * filled}[/{color}][dim]{'.' * empty}[/dim]"


def _build_final_summary(events: list[SimEvent]) -> None:
    complete = next((e for e in reversed(events) if e.kind == "complete"), None)
    if not complete:
        return

    d = complete.data
    stats = d.get("mesh_stats", {})
    cache = d.get("cache_stats", {})
    audit = d.get("audit_entries", [])
    attribution = d.get("attribution", [])
    compliance = d.get("compliance", {})

    console.rule("[bold cyan]AgentMesh Final Report[/bold cyan]")
    console.print()

    # Stats grid
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="dim")
    grid.add_column()
    grid.add_column(style="dim")
    grid.add_column()
    grid.add_row(
        "Total tokens",  f"[bold]{stats.get('tokens_used', 0):,}[/bold]",
        "Total cost",    f"[bold green]${stats.get('cost_usd', 0):.4f}[/bold green]",
    )
    grid.add_row(
        "Iterations",    f"[bold]{stats.get('iterations', 0)}[/bold]",
        "Model upgrades",f"[bold blue]{stats.get('model_upgrades', 0)}[/bold blue]",
    )
    grid.add_row(
        "Cache hit rate",f"[bold magenta]{cache.get('hit_rate', 0):.0%}[/bold magenta]",
        "Tokens saved",  f"[bold]{cache.get('tokens_saved', 0):,}[/bold]",
    )
    grid.add_row(
        "Audit entries", f"[bold]{stats.get('audit_entries', len(audit))}[/bold]",
        "Chain valid",   "[bold green]VERIFIED[/bold green]",
    )
    console.print(Panel(grid, title="Summary", border_style="cyan"))

    # Compliance
    fw = compliance.get("framework_name", "EU AI Act")
    rate = compliance.get("pass_rate", 0)
    result = "[bold green]COMPLIANT[/bold green]" if compliance.get("overall_compliant") else "[bold red]NON-COMPLIANT[/bold red]"
    console.print(Panel(
        f"[dim]Framework:[/dim] {fw}\n[dim]Pass rate:[/dim] [bold]{rate:.0%}[/bold]\n[dim]Result:[/dim]    {result}",
        title="[Compliance] EU AI Act",
        border_style="green" if compliance.get("overall_compliant") else "red",
    ))

    # Attribution
    if attribution:
        attr_table = Table(title="[$] Cost Attribution by Team", box=box.SIMPLE_HEAD, border_style="yellow")
        attr_table.add_column("Team",       style="cyan")
        attr_table.add_column("Calls",      justify="right")
        attr_table.add_column("Tokens",     justify="right")
        attr_table.add_column("Cost (USD)", justify="right", style="green")
        attr_table.add_column("Models")
        for row in attribution:
            attr_table.add_row(
                row["group_key"],
                str(row["call_count"]),
                f"{row['total_tokens']:,}",
                f"${row['total_cost_usd']:.4f}",
                ", ".join(row["unique_models"]),
            )
        console.print(attr_table)

    # Last 5 audit entries
    if audit:
        aud_table = Table(title="[Audit] Audit Trail (last 5 entries)", box=box.SIMPLE_HEAD, border_style="blue")
        aud_table.add_column("Time",       style="dim")
        aud_table.add_column("Event",      style="cyan")
        aud_table.add_column("Agent",      style="dim")
        aud_table.add_column("Model",      style="dim")
        aud_table.add_column("Tokens",     justify="right")
        for e in audit[-5:]:
            aud_table.add_row(
                e["timestamp"], e["event_type"], e["agent_id"], e["model"], str(e["tokens_used"])
            )
        console.print(aud_table)

    console.print()
    console.print("[dim]Tip: run[/dim] [bold]python examples/dashboard_web.py[/bold] [dim]for the full browser dashboard[/dim]")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_dashboard(scenario: str = "code-review", template: str = "enterprise", trip: bool = False) -> None:
    events: list[SimEvent] = []

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="stats",  ratio=2),
        Layout(name="budget", ratio=1),
        Layout(name="cache",  ratio=1),
    )
    layout["right"].name = "feed"

    def _refresh():
        layout["header"].update(_build_header_panel(scenario, template))
        layout["stats"].update(_build_stats_panel(events))
        layout["budget"].update(_build_budget_panel(events))
        layout["cache"].update(_build_cache_panel(events))
        layout["feed"].update(_build_event_feed(events))
        layout["footer"].update(Panel(
            f"[dim]Press Ctrl+C to stop  |  {len(events)} events  |  "
            f"iterations: {events[-1].iteration if events else 0}  |  "
            f"cost: ${events[-1].total_cost:.4f}[/dim]" if events else "[dim]Starting...[/dim]",
            style="dim",
        ))

    _refresh()

    with Live(layout, console=console, refresh_per_second=8, screen=True):
        gen = run_scenario(scenario, template, trip_circuit_breaker=trip)
        for event in gen:
            events.append(event)
            _refresh()
            time.sleep(0.05)  # brief pause so dashboard is readable

    console.clear()
    _build_final_summary(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentMesh Terminal Dashboard")
    parser.add_argument("--scenario", choices=SCENARIOS, default="code-review")
    parser.add_argument("--template", choices=TEMPLATES, default="enterprise")
    parser.add_argument("--trip-circuit-breaker", action="store_true",
                        help="Run enough iterations to trip the circuit breaker")
    args = parser.parse_args()
    run_dashboard(args.scenario, args.template, args.trip_circuit_breaker)


if __name__ == "__main__":
    main()
