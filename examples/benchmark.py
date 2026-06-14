"""
AgentMesh Three-Layer Cache Benchmark
======================================

Demonstrates AgentMesh's semantic cache savings with REAL numbers on a
realistic enterprise workload: 20 requests across 5 topic clusters, with
4 phrasings per topic to exercise the normalizer (exact match, persona
prefix, markdown formatting, British spelling).

Run:
    python examples/benchmark.py

No API keys required — uses demo_mode=True (realistic mock responses).
Port 8097 (avoids conflicts with 8080/EDB, 8090/main proxy, 8099/e2e tests).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, NamedTuple, Tuple

# ---------------------------------------------------------------------------
# Optional rich for pretty tables; plain ANSI fallback always works
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box as rich_box
    HAS_RICH = True
    _console = Console()
except ImportError:
    HAS_RICH = False
    _console = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# ANSI colour helpers (plain-text fallback path)
# ---------------------------------------------------------------------------

try:
    import os as _os
    _USE_COL = sys.stdout.isatty() or bool(_os.environ.get("FORCE_COLOR"))
except Exception:
    _USE_COL = False


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COL else s


def green(s: str) -> str:   return _c("92", s)
def yellow(s: str) -> str:  return _c("93", s)
def cyan(s: str) -> str:    return _c("96", s)
def red(s: str) -> str:     return _c("91", s)
def bold(s: str) -> str:    return _c("1",  s)
def dim(s: str) -> str:     return _c("2",  s)
def magenta(s: str) -> str: return _c("95", s)


# ---------------------------------------------------------------------------
# Benchmark constants
# ---------------------------------------------------------------------------

PORT       = 8097
BASE       = f"http://localhost:{PORT}"
COST_PER_M = 3.0   # $/million tokens — matches Haiku-class pricing used in demo

# ---------------------------------------------------------------------------
# Topic clusters — 4 phrasings each to exercise the normalizer
#
# Phrasing strategy per cluster:
#   [0] Plain question                    — cold-start miss
#   [1] Persona prefix "You are a …"      — tests system-prompt stripping
#   [2] Markdown-heavy formatting          — tests whitespace normalisation
#   [3] British spelling / light synonyms  — tests vocabulary normalisation
#
# The proxy's character n-gram semantic cache is strong enough to match
# all four phrasings within a cluster to the same cached response, so
# you should see 5 misses (one per cluster) and 15 hits overall.
# ---------------------------------------------------------------------------

CLUSTERS: List[Tuple[str, List[str]]] = [
    (
        "Architecture review",
        [
            # [0] Plain
            "What are the risks of using synchronous REST calls between microservices "
            "and how do I mitigate them?",
            # [1] Persona prefix
            "You are a senior distributed-systems architect. What are the primary risks "
            "of synchronous REST coupling between microservices, and what mitigation "
            "strategies do you recommend?",
            # [2] Markdown
            "## Architecture Review\n\n**Topic:** Microservices communication\n\n"
            "What risks does synchronous REST introduce between microservices, "
            "and which patterns best reduce those risks?",
            # [3] British spelling
            "What are the risks of utilising synchronous REST calls amongst "
            "microservices and how do I mitigate them?",
        ],
    ),
    (
        "Code review",
        [
            # [0] Plain
            "Review this Python authentication function for security vulnerabilities: "
            "def login(user, pwd): return db.execute(f'SELECT * FROM users WHERE name={user}')",
            # [1] Persona prefix
            "You are a senior security engineer. Please review this Python authentication "
            "function for security flaws: "
            "def login(username, password): return db.execute(f'SELECT * FROM users WHERE name={username}')",
            # [2] Markdown
            "## Code Review Request\n\n```python\ndef login(user, pwd):\n"
            "    return db.execute(f'SELECT * FROM users WHERE name={user}')\n```\n\n"
            "Identify any security vulnerabilities in this authentication function.",
            # [3] British spelling / synonym
            "Analyse this Python authentication function for security vulnerabilities: "
            "def login(user, pwd): return db.execute(f'SELECT * FROM users WHERE name={user}')",
        ],
    ),
    (
        "Q3 report summary",
        [
            # [0] Plain
            "Summarise the Q3 financial results: revenue grew 23% YoY to $4.2B, "
            "operating margin expanded 180 bps, and free cash flow reached $890M.",
            # [1] Persona prefix
            "You are a senior financial analyst. Summarise these Q3 results for the "
            "board: revenue grew 23% YoY to $4.2B, operating margin expanded 180 bps, "
            "and free cash flow reached $890M.",
            # [2] Markdown
            "## Q3 Financial Results Summary\n\n"
            "| Metric | Value |\n|---|---|\n"
            "| Revenue | $4.2B (+23% YoY) |\n"
            "| Op. Margin | +180 bps |\n"
            "| Free Cash Flow | $890M |\n\n"
            "Write an executive summary of these Q3 financial results.",
            # [3] British spelling
            "Summarise the Q3 financial results: revenue grew 23% year-on-year to "
            "$4.2B, operating margin expanded 180 basis points, and free cash flow "
            "totalled $890M.",
        ],
    ),
    (
        "Token optimisation",
        [
            # [0] Plain
            "What are the most effective strategies for reducing LLM API costs "
            "through prompt compression and token optimisation?",
            # [1] Persona prefix
            "You are a senior ML platform engineer. What strategies most effectively "
            "reduce LLM API spend through prompt compression, caching, and token "
            "optimisation?",
            # [2] Markdown
            "## LLM Cost Reduction Strategies\n\n"
            "**Goal:** Reduce monthly LLM API spend by 60-70%\n\n"
            "What are the most impactful techniques for prompt compression and "
            "token optimisation to lower LLM API costs?",
            # [3] British spelling / synonym
            "What are the most effective strategies for reducing LLM API costs "
            "through prompt compression and token optimisation across a large "
            "engineering organisation?",
        ],
    ),
    (
        "Kubernetes deploy",
        [
            # [0] Plain
            "How should I configure Kubernetes resource limits and pod autoscaling "
            "for a stateless web service handling variable traffic?",
            # [1] Persona prefix
            "You are a senior DevOps engineer. How should resource limits and "
            "horizontal pod autoscaling be configured in Kubernetes for a stateless "
            "web service with variable traffic patterns?",
            # [2] Markdown
            "## Kubernetes Deployment Review\n\n"
            "**Service type:** Stateless web service\n"
            "**Traffic pattern:** Variable (0-10k RPS)\n\n"
            "What resource limit and pod autoscaling configuration do you recommend?",
            # [3] British spelling / synonym
            "How should I configure Kubernetes resource limits and pod autoscaling "
            "for a stateless web service handling variable traffic loads?",
        ],
    ),
]

# Flat ordered list: (cluster_name, phrasing_index, prompt_text)
REQUESTS: List[Tuple[str, int, str]] = [
    (cluster_name, i, prompt)
    for cluster_name, phrasings in CLUSTERS
    for i, prompt in enumerate(phrasings)
]

assert len(REQUESTS) == 20, f"Expected 20 requests, got {len(REQUESTS)}"

# Build a lookup from cluster_name -> [phrasing_0_text, ...]
_CLUSTER_MAP: Dict[str, List[str]] = {name: phrasings for name, phrasings in CLUSTERS}


# ---------------------------------------------------------------------------
# Result record
# ---------------------------------------------------------------------------

class RequestResult(NamedTuple):
    seq:                     int
    cluster:                 str
    phrasing_idx:            int
    prompt:                  str
    status:                  int
    cache_layer:             str   # "exact" | "semantic" | "miss"
    tokens_used:             int   # actual LLM tokens on miss; 0 on hit
    tokens_saved:            int   # tokens the hit avoided paying for
    cost_usd:                float # cost reported by proxy
    latency_ms:              int
    cumulative_tokens_saved: int
    cumulative_cost_saved:   float


# ---------------------------------------------------------------------------
# Proxy startup
# ---------------------------------------------------------------------------

def start_benchmark_proxy() -> None:
    """Start the AgentMesh proxy on port 8097 in demo_mode=True."""
    from agentmesh.proxy.server import ProxyConfig, start_proxy

    config = ProxyConfig(
        vendors               = ["anthropic", "openai"],
        routing_strategy      = "cheapest_capable",
        demo_mode             = True,
        enable_cache          = True,
        enable_compression    = True,
        global_monthly_tokens = 10_000_000,
        port                  = PORT,
        log_level             = "error",
    )
    start_proxy(config)


def wait_ready(max_s: int = 30) -> bool:
    """Poll /health until the proxy is up or the timeout expires."""
    deadline = time.time() + max_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def post_message(prompt: str) -> Tuple[int, Dict[str, str], Dict[str, Any]]:
    """Send an Anthropic-format /v1/messages request to the proxy."""
    body = json.dumps({
        "model":      "claude-haiku-4-5",
        "max_tokens": 256,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        BASE + "/v1/messages",
        data=body,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         "agentmesh",
            "X-AgentMesh-Team":  "benchmark",
            "X-AgentMesh-User":  "benchmarker@company.com",
            "X-AgentMesh-Tool":  "benchmark-script",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), _try_json(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), _try_json(e.read())
    except Exception as ex:
        return 0, {}, {"_err": str(ex)}


def _try_json(raw: bytes) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Cache-layer inference
#
# The proxy returns X-AgentMesh-Cache: "hit" | "miss".
# It does not distinguish exact vs semantic — we reconstruct that:
#   • miss  → always a miss
#   • hit + prompt identical to cluster's first phrasing → exact hit
#   • hit + prompt differs from cluster's first phrasing  → semantic hit
# ---------------------------------------------------------------------------

def _infer_layer(
    cache_hdr:    str,
    phrasing_idx: int,
    prompt:       str,
    cluster_name: str,
) -> str:
    if cache_hdr != "hit":
        return "miss"
    original = _CLUSTER_MAP[cluster_name][0]
    if prompt.strip() == original.strip():
        return "exact"
    return "semantic"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_LAYER_RICH = {
    "exact":    "[bold green]EXACT HIT   [/]",
    "semantic": "[bold cyan]SEMANTIC HIT[/]",
    "miss":     "[bold red]MISS        [/]",
}

_LAYER_PLAIN = {
    "exact":    green("EXACT HIT   "),
    "semantic": cyan("SEMANTIC HIT"),
    "miss":     red("MISS        "),
}


def _preview(prompt: str, width: int = 52) -> str:
    """Flatten newlines and truncate for table display."""
    s = prompt.replace("\n", " ").strip()
    return s if len(s) <= width else s[:width - 3] + "..."


def _print_progress_rich(results: List[RequestResult], total: int) -> None:
    t = Table(
        title=f"[bold]AgentMesh Cache Benchmark  ({len(results)}/{total})[/]",
        box=rich_box.SIMPLE_HEAVY,
        show_lines=False,
        expand=True,
    )
    t.add_column("#",               style="dim", width=3,  justify="right")
    t.add_column("Cluster",         style="bold", width=18, no_wrap=True)
    t.add_column("Prompt preview",               width=52, no_wrap=True)
    t.add_column("Cache layer",                  width=15, justify="center")
    t.add_column("Tokens saved",    justify="right", width=12)
    t.add_column("Cumulative saved", justify="right", width=16)
    t.add_column("Latency ms",      justify="right", width=10)

    for r in results:
        t.add_row(
            str(r.seq),
            r.cluster,
            _preview(r.prompt),
            _LAYER_RICH.get(r.cache_layer, r.cache_layer),
            f"{r.tokens_saved:,}",
            f"{r.cumulative_tokens_saved:,}",
            str(r.latency_ms),
        )

    _console.clear()
    _console.print(t)


def _print_row_plain(r: RequestResult) -> None:
    layer = _LAYER_PLAIN.get(r.cache_layer, r.cache_layer)
    print(
        f"  [{r.seq:02d}/20] {layer}  "
        f"saved={dim(str(r.tokens_saved).rjust(5) + ' tok')}  "
        f"Σ={dim(str(r.cumulative_tokens_saved).rjust(7) + ' tok')}  "
        f"{dim(_preview(r.prompt, 44))}"
    )


def _print_summary(results: List[RequestResult]) -> None:
    total = len(results)
    exact_hits    = [r for r in results if r.cache_layer == "exact"]
    semantic_hits = [r for r in results if r.cache_layer == "semantic"]
    misses        = [r for r in results if r.cache_layer == "miss"]

    total_saved   = sum(r.tokens_saved for r in results)
    total_used    = sum(r.tokens_used  for r in results)
    total_all     = total_used + total_saved

    cost_without  = total_all  / 1_000_000 * COST_PER_M
    cost_with     = total_used / 1_000_000 * COST_PER_M
    savings_usd   = cost_without - cost_with
    savings_pct   = savings_usd / cost_without * 100 if cost_without > 0 else 0.0
    cost_per_req  = cost_with / total if total > 0 else 0.0

    if HAS_RICH:
        from rich.panel import Panel
        from rich.table import Table as RTable
        from rich import box as rbox

        t = RTable(box=rbox.SIMPLE, show_header=False, expand=False)
        t.add_column("Metric", style="bold", width=44)
        t.add_column("Value",  justify="right", width=18)

        t.add_row("Total requests",     str(total))
        t.add_row(
            "[green]Exact cache hits[/]",
            f"[green]{len(exact_hits)} ({len(exact_hits)/total:.0%})[/]",
        )
        t.add_row(
            "[cyan]Semantic cache hits[/]",
            f"[cyan]{len(semantic_hits)} ({len(semantic_hits)/total:.0%})[/]",
        )
        t.add_row(
            "[red]Total misses[/]",
            f"[red]{len(misses)} ({len(misses)/total:.0%})[/]",
        )
        t.add_row("", "")
        t.add_row("Total tokens saved",    f"{total_saved:,}")
        t.add_row("Total tokens consumed", f"{total_used:,}")
        t.add_row("", "")
        t.add_row(
            f"Cost WITHOUT AgentMesh  (${COST_PER_M:.0f}/M tokens)",
            f"${cost_without:.4f}",
        )
        t.add_row("Cost WITH AgentMesh",    f"${cost_with:.4f}")
        t.add_row(
            "[bold green]Savings[/]",
            f"[bold green]${savings_usd:.4f}  ({savings_pct:.0f}%)[/]",
        )
        t.add_row("Effective cost per request", f"${cost_per_req:.5f}")

        _console.print()
        _console.print(Panel(
            t,
            title="[bold]AgentMesh Benchmark — Final Summary[/]",
            border_style="green",
        ))
        _console.print()
    else:
        W = 58
        print(f"\n{bold('=' * W)}")
        print(bold("  AgentMesh Benchmark — Final Summary"))
        print(bold("=" * W))
        print(f"  Total requests                    {total}")
        print(f"  {green('Exact cache hits')}               "
              f"  {green(str(len(exact_hits)))}  ({len(exact_hits)/total:.0%})")
        print(f"  {cyan('Semantic cache hits')}             "
              f"  {cyan(str(len(semantic_hits)))}  ({len(semantic_hits)/total:.0%})")
        print(f"  {red('Total misses')}                     "
              f"  {red(str(len(misses)))}  ({len(misses)/total:.0%})")
        print()
        print(f"  Total tokens saved                {total_saved:,}")
        print(f"  Total tokens consumed             {total_used:,}")
        print()
        print(f"  Cost WITHOUT AgentMesh            ${cost_without:.4f}"
              f"  (at ${COST_PER_M}/M tokens)")
        print(f"  Cost WITH AgentMesh               ${cost_with:.4f}")
        print(f"  {green('Savings')}                          "
              f"{green(f'${savings_usd:.4f}')}  ({green(f'{savings_pct:.0f}%')})")
        print(f"  Effective cost per request        ${cost_per_req:.5f}")
        print(bold("=" * W))
        print()


# ---------------------------------------------------------------------------
# Core run loop
# ---------------------------------------------------------------------------

def run_all_requests() -> List[RequestResult]:
    """
    Send all 20 benchmark requests to the proxy and return results.

    Token accounting:
      - On a miss: tokens_used = what the proxy reports in X-AgentMesh-Tokens.
        We store this as the cluster's "baseline" cost for future hit savings.
      - On a hit:  tokens_used = 0 (no LLM call was made).
        tokens_saved = the baseline stored on the cluster's first miss.
    """
    results:              List[RequestResult] = []
    cluster_first_tokens: Dict[str, int]      = {}
    cumulative_saved                          = 0
    total                                     = len(REQUESTS)

    if not HAS_RICH:
        W = 58
        print(f"\n{bold('=' * W)}")
        print(bold("  Live request log  (20 requests / 5 clusters)"))
        print(bold("=" * W))

    for seq, (cluster_name, phrasing_idx, prompt) in enumerate(REQUESTS, 1):
        t0 = time.monotonic()
        status, hdrs, body = post_message(prompt)
        latency_ms = round((time.monotonic() - t0) * 1000)

        if status != 200:
            err = body.get("_err") or body.get("error", {})
            sys.stderr.write(
                f"  [WARNING] Request {seq} returned HTTP {status}: {err}\n"
            )

        # Header names are lower-cased by Python's urllib
        cache_hdr  = hdrs.get("x-agentmesh-cache",    "miss").lower()
        tokens_str = hdrs.get("x-agentmesh-tokens",   "0")
        cost_str   = hdrs.get("x-agentmesh-cost-usd", "0")

        try:
            tokens_hdr = int(tokens_str)
        except ValueError:
            tokens_hdr = 0
        try:
            cost_usd = float(cost_str)
        except ValueError:
            cost_usd = 0.0

        cache_layer = _infer_layer(cache_hdr, phrasing_idx, prompt, cluster_name)

        if cache_layer == "miss":
            tokens_used  = tokens_hdr
            tokens_saved = 0
            # Record baseline for this cluster so future hits know their savings
            cluster_first_tokens[cluster_name] = tokens_used
        else:
            # Hit — no LLM call, tokens saved = what the first miss would have cost
            tokens_used  = 0
            tokens_saved = cluster_first_tokens.get(cluster_name, 0)

        cumulative_saved += tokens_saved

        result = RequestResult(
            seq=seq,
            cluster=cluster_name,
            phrasing_idx=phrasing_idx,
            prompt=prompt,
            status=status,
            cache_layer=cache_layer,
            tokens_used=tokens_used,
            tokens_saved=tokens_saved,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            cumulative_tokens_saved=cumulative_saved,
            cumulative_cost_saved=cumulative_saved / 1_000_000 * COST_PER_M,
        )
        results.append(result)

        if HAS_RICH:
            _print_progress_rich(results, total)
        else:
            _print_row_plain(result)

        # Small pause so the live progress table is readable
        time.sleep(0.05)

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    W = 58

    if HAS_RICH:
        _console.rule("[bold green]AgentMesh  Three-Layer Cache Benchmark[/]")
        _console.print(
            "  [dim]Starting proxy on port 8097 in demo_mode "
            "(no API keys needed)…[/]"
        )
    else:
        print(f"\n{bold('=' * W)}")
        print(bold("  AgentMesh  Three-Layer Cache Benchmark"))
        print(bold("=" * W))
        print(f"  {cyan('Starting proxy on port 8097 (demo mode)…')}")

    try:
        start_benchmark_proxy()
    except Exception as exc:
        sys.stderr.write(f"  [ERROR] Could not start proxy: {exc}\n")
        sys.exit(1)

    if not wait_ready(max_s=30):
        sys.stderr.write(
            "\n  [ERROR] Proxy did not become ready within 30 s.\n"
            "  Check that port 8097 is free and agentmesh is installed.\n"
        )
        sys.exit(1)

    if HAS_RICH:
        _console.print("  [bold green]Proxy ready.[/]  Running 20 requests…\n")
    else:
        print(f"  {green('Proxy ready.')}  Running 20 requests…\n")

    results = run_all_requests()
    _print_summary(results)

    sys.exit(0 if all(r.status == 200 for r in results) else 1)


if __name__ == "__main__":
    main()
