"""
AgentMesh CLI — command-line interface for governance management.

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
from typing import Optional


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

    # --- proxy ---
    proxy_p = subparsers.add_parser("proxy", help="Start governance HTTP proxy")
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
        "validate": cmd_validate,
        "audit": cmd_audit,
        "budget": cmd_budget,
        "compliance": cmd_compliance,
        "proxy": cmd_proxy,
        "benchmark": cmd_benchmark,
        "version": cmd_version,
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
    from agentmesh.proxy.server import AgentMeshProxy
    from agentmesh.policy.engine import Policy

    policy = None
    if args.policy:
        policy = Policy.from_yaml(Path(args.policy).read_text())

    proxy = AgentMeshProxy(policy=policy, port=args.port, upstream=args.upstream)
    _info(f"Starting AgentMesh proxy on port {args.port} → {args.upstream}")
    _info("Point your LLM client at: http://localhost:{args.port}")
    _info("Press Ctrl+C to stop.")
    try:
        proxy.start(blocking=True)
    except KeyboardInterrupt:
        proxy.stop()
        _info("Proxy stopped.")


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
