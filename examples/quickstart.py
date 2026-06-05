"""
AgentMesh Quickstart — wrap a LangGraph agent with governance in 5 lines.

Run: python examples/quickstart.py
"""

from agentmesh import AgentMesh
from agentmesh.policy import Policy


def main():
    # 1. Define your governance policy
    policy = Policy.from_dict({
        "name": "quickstart-demo",
        "budget": {
            "per_run_tokens": 10_000,
            "hard_stop": True,
        },
        "model_routing": {
            "default": "claude-haiku-4-5",
            "max_allowed": "claude-sonnet-4-6",
        },
        "circuit_breaker": {
            "max_iterations": 10,
        },
    })

    # 2. Initialize AgentMesh
    mesh = AgentMesh(policy=policy)

    print("AgentMesh initialized:")
    print(f"  Policy: {mesh.policy.name}")
    print(f"  Budget: {policy.schema.budget.per_run_tokens:,} tokens per run")
    print(f"  Circuit breaker: {policy.schema.circuit_breaker.max_iterations} max iterations")
    print(f"  Compression threshold: {policy.schema.optimization.compression_threshold:.0%}")
    print()

    # 3. Simulate a governed call (without a real LLM for demo purposes)
    print("Simulating budget tracking...")

    class MockResponse:
        class usage:
            input_tokens = 1_200
            output_tokens = 800
        model = "claude-haiku-4-5"

    mesh.budget.reset_run()
    for i in range(3):
        mesh.circuit_breaker.check()
        mesh.budget.check_pre_call({"model": "claude-haiku-4-5"})
        mesh.budget.record_usage(MockResponse())
        mesh.circuit_breaker.increment()
        print(f"  Iteration {i+1}: {mesh.stats['tokens_used']:,} tokens used, "
              f"${mesh.stats['cost_usd']:.4f} cost")

    print()
    print("Final stats:", mesh.stats)

    # 4. Show audit trail
    audit_entries = mesh.audit.entries
    print(f"\nAudit trail: {len(audit_entries)} entries recorded")
    print(f"Chain integrity: {'✓ VALID' if mesh.audit.verify() else '✗ INVALID'}")


if __name__ == "__main__":
    main()
