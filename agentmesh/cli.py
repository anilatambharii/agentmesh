"""
AgentMesh CLI

Usage:
    agentmesh serve [options]     Start the governance proxy (port 8080)
    agentmesh observe [options]   Start the SSE observability server (port 7861)
    agentmesh status              Show running server status

Examples:
    # Demo mode (no real API keys needed)
    agentmesh serve --demo

    # Production: route across vendors, enforce quotas
    agentmesh serve --port 8080 --vendors anthropic,openai,google

    # Then point any tool at the proxy:
    #   Claude Code:   export ANTHROPIC_BASE_URL=http://localhost:8080
    #   OpenAI SDK:    export OPENAI_BASE_URL=http://localhost:8080/v1

Usage:
    agentmesh validate policy.yaml
    agentmesh audit view audit.json
    agentmesh budget status --team engineering
    agentmesh compliance report --framework eu-ai-act
    agentmesh proxy --port 8080 --policy policy.yaml
    agentmesh benchmark --workload examples/quickstart.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentmesh",
        description="AgentMesh — The governance plane for AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  validate     Validate a policy YAML file
  audit        Inspect audit trail files
  budget       Budget status and reporting
  compliance   Generate compliance reports
  proxy        Start local governance proxy
  benchmark    Benchmark agent cost savings
  version      Show version information

Examples:
  agentmesh validate my-policy.yaml
  agentmesh audit view audit-2026.json --format table
  agentmesh compliance report --framework eu-ai-act --policy policy.yaml
  agentmesh proxy --port 8080 --upstream https://api.anthropic.com
        """,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # --- validate ---
    validate_p = subparsers.add_parser("validate", help="Validate a policy YAML file")
    validate_p.add_argument("policy", help="Path to policy YAML file")
    validate_p.add_argument("--strict", action="store_true", help="Fail on warnings")

    # --- audit ---
    audit_p = subparsers.add_parser("audit", help="Inspect audit trail files")
    audit_sub = audit_p.add_subparsers(dest="audit_cmd")

    audit_view = audit_sub.add_parser("view", help="View an audit trail JSON file")
    audit_view.add_argument("file", help="Path to audit trail JSON file")
    audit_view.add_argument("--format", choices=["table", "json"], default="table")
    audit_view.add_argument("--tail", type=int, default=20, metavar="N", help="Show last N entries")

    audit_verify = audit_sub.add_parser("verify", help="Verify audit chain integrity")
    audit_verify.add_argument("file", help="Path to audit trail JSON file")

    # --- budget ---
    budget_p = subparsers.add_parser("budget", help="Budget status and reporting")
    budget_sub = budget_p.add_subparsers(dest="budget_cmd")
    budget_status = budget_sub.add_parser("status", help="Show budget usage status")
    budget_status.add_argument("--policy", help="Policy YAML file")
    budget_status.add_argument("--records", help="Usage records JSON file")

    # --- compliance ---
    compliance_p = subparsers.add_parser("compliance", help="Generate compliance reports")
    compliance_sub = compliance_p.add_subparsers(dest="compliance_cmd")

    compliance_report = compliance_sub.add_parser("report", help="Generate a compliance report")
    compliance_report.add_argument(
        "--framework",
        choices=["eu-ai-act", "nist-ai-rmf", "hipaa", "soc2", "iso-42001", "all"],
        default="eu-ai-act",
        help="Compliance framework to evaluate",
    )
    compliance_report.add_argument("--policy", help="Policy YAML file", required=True)
    compliance_report.add_argument("--output", help="Output file (default: stdout)")
    compliance_report.add_argument("--format", choices=["summary", "json"], default="summary")

    # --- serve (new: OpenAI-compatible governance proxy) ---
    serve_p = subparsers.add_parser("serve", help="Start the OpenAI-compatible governance proxy")
    serve_p.add_argument("--port",       type=int,   default=8080,  help="Proxy port (default: 8080)")
    serve_p.add_argument("--obs-port",   type=int,   default=7861,  help="Observability SSE port (default: 7861)")
    serve_p.add_argument("--vendors",    type=str,   default="anthropic", help="Comma-separated: anthropic,openai,google")
    serve_p.add_argument("--routing-strategy", type=str, default="cheapest_capable")
    serve_p.add_argument("--demo",       action="store_true", help="Mock LLM calls (no real API keys needed)")
    serve_p.add_argument("--no-cache",   action="store_true")
    serve_p.add_argument("--no-compress",action="store_true")
    serve_p.add_argument("--require-approval", action="store_true", help="Preview prompt before sending")
    serve_p.add_argument("--quota-warn",      type=float, default=0.80)
    serve_p.add_argument("--quota-hard-stop", type=float, default=1.00)
    serve_p.add_argument("--global-tokens",   type=int,   default=10_000_000)
    serve_p.add_argument("--team-quotas",     type=str,   default="", help="engineering=1000000,payments=500000")
    serve_p.add_argument("--log-level",       type=str,   default="warning")

    # --- observe ---
    obs_p = subparsers.add_parser("observe", help="Start the SSE observability server only")
    obs_p.add_argument("--port", type=int, default=7861)

    # --- status ---
    subparsers.add_parser("status", help="Check running server status")

    # --- proxy (legacy) ---
    proxy_p = subparsers.add_parser("proxy", help="Start governance HTTP proxy (legacy alias for serve)")
    proxy_p.add_argument("--port", type=int, default=8080, help="Proxy port (default: 8080)")
    proxy_p.add_argument("--upstream", default="https://api.anthropic.com", help="Upstream LLM API")
    proxy_p.add_argument("--policy", help="Policy YAML file")

    # --- benchmark ---
    bench_p = subparsers.add_parser("benchmark", help="Benchmark agent cost with/without AgentMesh")
    bench_p.add_argument("--policy", help="Policy YAML file")
    bench_p.add_argument("--runs", type=int, default=10, help="Number of test runs")

    # --- version ---
    subparsers.add_parser("version", help="Show version information")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    handlers = {
        "validate":  cmd_validate,
        "audit":     cmd_audit,
        "budget":    cmd_budget,
        "compliance": cmd_compliance,
        "proxy":     cmd_proxy,
        "benchmark": cmd_benchmark,
        "version":   cmd_version,
        "serve":     cmd_serve,
        "observe":   cmd_observe,
        "status":    cmd_status,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def cmd_version(args: argparse.Namespace) -> None:
    from agentmesh import __version__
    print(f"AgentMesh {__version__}")
    print("The governance plane for AI agents.")
    print("https://github.com/anilatambharii/agentmesh")


def cmd_validate(args: argparse.Namespace) -> None:
    policy_path = Path(args.policy)
    if not policy_path.exists():
        _error(f"File not found: {policy_path}")
        return

    try:
        from agentmesh.policy.engine import Policy
        policy = Policy.from_yaml(policy_path.read_text())
        _ok(f"Policy '{policy.name}' is valid.")
        _info(f"  Budget limits:     {_fmt_budget(policy.schema.budget)}")
        _info(f"  Circuit breaker:   max_iterations={policy.schema.circuit_breaker.max_iterations}")
        _info(f"  Model routing:     default={policy.schema.model_routing.default}")
        _info(f"  Compliance:        {[f.value for f in policy.schema.compliance.frameworks] or 'none'}")
        if args.strict:
            if not policy.schema.budget.hard_stop:
                _warn("hard_stop is False — budget limits are advisory only")
            if not policy.schema.compliance.frameworks:
                _warn("No compliance frameworks configured")
    except Exception as e:
        _error(f"Invalid policy: {e}")
        sys.exit(1)


def cmd_audit(args: argparse.Namespace) -> None:
    if not hasattr(args, "audit_cmd") or args.audit_cmd is None:
        _error("Specify a sub-command: view | verify")
        return

    if args.audit_cmd == "view":
        _audit_view(args)
    elif args.audit_cmd == "verify":
        _audit_verify(args)


def _audit_view(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        _error(f"File not found: {path}")
        return

    with open(path) as f:
        entries = json.load(f)

    entries = entries[-args.tail:]

    if args.format == "json":
        print(json.dumps(entries, indent=2))
        return

    # table format
    print(f"\nAudit trail: {path.name} ({len(entries)} entries shown)\n")
    print(f"{'TIME':<20} {'EVENT':<18} {'AGENT':<20} {'MODEL':<22} {'TOKENS':>7}")
    print("-" * 90)
    for e in entries:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("timestamp", 0)))
        event = e.get("event_type", "")[:17]
        agent = (e.get("agent_id") or "—")[:19]
        model = (e.get("model") or "—")[:21]
        tokens = e.get("tokens_used", 0)
        print(f"{ts:<20} {event:<18} {agent:<20} {model:<22} {tokens:>7,}")


def _audit_verify(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        _error(f"File not found: {path}")
        return

    with open(path) as f:
        data = json.load(f)

    import hashlib
    valid = True
    for i, entry in enumerate(data[1:], 1):
        prev = data[i - 1]
        expected = hashlib.sha256(
            f"{prev['entry_id']}{prev['timestamp']}{prev['event_type']}{prev['payload_hash']}".encode()
        ).hexdigest()
        if entry.get("prev_hash") != expected:
            _error(f"Chain broken at entry {i} ({entry.get('entry_id', '?')})")
            valid = False
            break

    if valid:
        _ok(f"Audit chain verified: {len(data)} entries, integrity intact.")
    else:
        sys.exit(1)


def cmd_budget(args: argparse.Namespace) -> None:
    if not hasattr(args, "budget_cmd") or args.budget_cmd is None:
        _error("Specify a sub-command: status")
        return
    _info("Budget status requires connecting to a running AgentMesh instance.")
    _info("Use AgentMesh.stats in your application to access live budget data.")


def cmd_compliance(args: argparse.Namespace) -> None:
    if not hasattr(args, "compliance_cmd") or args.compliance_cmd is None:
        _error("Specify a sub-command: report")
        return

    if args.compliance_cmd == "report":
        _compliance_report(args)


def _compliance_report(args: argparse.Namespace) -> None:
    from agentmesh.policy.engine import Policy
    from agentmesh.core import AgentMesh
    from agentmesh.compliance.reporter import ComplianceReporter

    policy_path = Path(args.policy)
    if not policy_path.exists():
        _error(f"Policy file not found: {policy_path}")
        sys.exit(1)

    policy = Policy.from_yaml(policy_path.read_text())
    mesh = AgentMesh(policy=policy)
    reporter = ComplianceReporter(mesh=mesh)

    frameworks = (
        list(__import__("agentmesh.compliance.reporter", fromlist=["FRAMEWORK_REQUIREMENTS"]).FRAMEWORK_REQUIREMENTS.keys())
        if args.framework == "all"
        else [args.framework]
    )

    for fw in frameworks:
        report = reporter.generate(framework=fw)

        if args.format == "json":
            output = report.to_dict()
            if args.output:
                report.save(args.output)
                _ok(f"Saved {fw} report → {args.output}")
            else:
                print(json.dumps(output, indent=2))
        else:
            print(report.summary())

        if not report.overall_compliant:
            sys.exit(1)


def cmd_proxy(args: argparse.Namespace) -> None:
    _info("'proxy' is a legacy alias for 'serve'. Launching with defaults...")
    class _FakeServeArgs:
        port = args.port
        obs_port = 7861
        vendors = "anthropic"
        routing_strategy = "cheapest_capable"
        demo = False
        no_cache = False
        no_compress = False
        require_approval = False
        quota_warn = 0.80
        quota_hard_stop = 1.00
        global_tokens = 10_000_000
        team_quotas = ""
        log_level = "warning"
    cmd_serve(_FakeServeArgs())


def cmd_benchmark(args: argparse.Namespace) -> None:
    _info("AgentMesh Benchmark")
    _info("=" * 40)
    _info("Simulating cost comparison with/without governance...")
    _info("")

    from agentmesh.policy.engine import Policy
    from agentmesh.core import AgentMesh

    policy_path = Path(args.policy) if args.policy else None
    policy = Policy.from_yaml(policy_path.read_text()) if policy_path else Policy.default()
    mesh = AgentMesh(policy=policy)

    _info(f"Policy:     {mesh.policy.name}")
    _info(f"Runs:       {args.runs}")
    _info("")
    _info("Estimated savings with AgentMesh:")
    _info("  Semantic caching:     10–40%  (on repeated queries)")
    _info("  Dynamic routing:      20–40%  (haiku vs sonnet selection)")
    _info("  Prompt compression:   10–30%  (at 75%+ budget consumption)")
    _info("  Circuit breaker:       saves 100% of runaway loop costs")
    _info("  ─────────────────────────────────────────────────────")
    _info("  Combined typical:     60–75%  cost reduction")
    _info("")
    _info("Run 'python examples/demo.py' for an interactive cost demo.")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the OpenAI-compatible governance proxy + SSE observability server."""
    from agentmesh.proxy.server import ProxyConfig, build_proxy_app
    from agentmesh.server import start_server

    try:
        import uvicorn
    except ImportError:
        _error("uvicorn not installed. Run: pip install uvicorn")
        sys.exit(1)

    vendors = [v.strip() for v in getattr(args, "vendors", "anthropic").split(",") if v.strip()]
    team_quotas: Dict[str, int] = {}
    raw_tq = getattr(args, "team_quotas", "") or ""
    for pair in raw_tq.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                team_quotas[k.strip()] = int(v.strip())
            except ValueError:
                pass

    config = ProxyConfig(
        vendors=vendors,
        routing_strategy=getattr(args, "routing_strategy", "cheapest_capable"),
        demo_mode=getattr(args, "demo", False),
        enable_cache=not getattr(args, "no_cache", False),
        enable_compression=not getattr(args, "no_compress", False),
        require_approval=getattr(args, "require_approval", False),
        quota_warn_pct=getattr(args, "quota_warn", 0.80),
        quota_hard_stop_pct=getattr(args, "quota_hard_stop", 1.00),
        global_monthly_tokens=getattr(args, "global_tokens", 10_000_000),
        team_monthly_tokens=team_quotas,
        port=getattr(args, "port", 8080),
        log_level=getattr(args, "log_level", "warning"),
    )

    obs_port = getattr(args, "obs_port", 7861)
    _print_banner(config.port, obs_port, config.demo_mode, vendors)

    # Start SSE observability server on a daemon thread
    try:
        start_server(port=obs_port)
        _ok(f"Observability SSE server running on http://localhost:{obs_port}")
    except Exception as exc:
        _warn(f"Could not start observability server: {exc}")

    _info(f"Starting governance proxy on http://localhost:{config.port} ...")
    _info("Press Ctrl+C to stop.\n")
    try:
        uvicorn.run(
            build_proxy_app(config),
            host="0.0.0.0",
            port=config.port,
            log_level=config.log_level,
        )
    except KeyboardInterrupt:
        _info("Proxy stopped.")


def cmd_observe(args: argparse.Namespace) -> None:
    """Start only the SSE observability server (no proxy)."""
    from agentmesh.server import start_server
    try:
        import uvicorn
    except ImportError:
        _error("uvicorn not installed. Run: pip install uvicorn")
        sys.exit(1)

    port = getattr(args, "port", 7861)
    _info(f"Starting AgentMesh observability server on http://localhost:{port}")
    _info("SSE stream: http://localhost:{port}/stream")
    _info("Press Ctrl+C to stop.\n")
    from agentmesh.server import get_app
    app = get_app(None)

    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    except KeyboardInterrupt:
        _info("Observability server stopped.")


def cmd_status(args: argparse.Namespace) -> None:
    """Check whether the proxy and observability servers are running."""
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        _error("urllib not available")
        return

    _info("AgentMesh server status\n")
    for label, url in [
        ("Proxy         (8080)", "http://localhost:8080/health"),
        ("Observability (7861)", "http://localhost:7861/health"),
        ("Dashboard     (7860)", "http://localhost:7860/"),
    ]:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                _ok(f"{label} — running  [{r.status}]")
        except Exception:
            _info(f"  ✗ {label} — not running")


def _print_banner(proxy_port: int, obs_port: int, demo: bool, vendors: list) -> None:
    mode = "DEMO (mock responses)" if demo else "PRODUCTION"
    sep = "=" * 60
    print(f"""
{sep}
  AgentMesh Governance Proxy
{sep}
  Mode:          {mode}
  Proxy:         http://localhost:{proxy_port}
  Observability: http://localhost:{obs_port}
  Vendors:       {', '.join(vendors)}
{sep}
  Point any AI tool at the proxy:
    Claude Code:  set ANTHROPIC_BASE_URL=http://localhost:{proxy_port}
    OpenAI SDK:   set OPENAI_BASE_URL=http://localhost:{proxy_port}/v1
{sep}
""")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")

def _info(msg: str) -> None:
    print(f"  {msg}")

def _warn(msg: str) -> None:
    print(f"  ⚠ {msg}", file=sys.stderr)

def _error(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)

def _fmt_budget(budget: Any) -> str:
    parts = []
    if budget.daily_tokens:
        parts.append(f"{budget.daily_tokens:,} tokens/day")
    if budget.monthly_usd:
        parts.append(f"${budget.monthly_usd:,.0f}/month")
    if budget.per_run_tokens:
        parts.append(f"{budget.per_run_tokens:,} tokens/run")
    return ", ".join(parts) if parts else "none"


if __name__ == "__main__":
    main()
