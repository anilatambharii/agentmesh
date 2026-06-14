"""
AgentMesh Simulation Engine — Enterprise Code Review Agent

Simulates a multi-step agentic code review workflow used by a 50-engineer team.
The LLM is mocked with realistic responses, token counts, and latencies.
ALL AgentMesh governance layers fire for real:
  - Budget enforcement (with hard stop)
  - Semantic cache (real cosine similarity — cache hits occur naturally)
  - Model routing (complexity signals drive haiku→sonnet upgrades)
  - Prompt compression (fires when budget gets low)
  - Circuit breaker (trip scenario available)
  - Audit trail (Ed25519-chained entries)
  - Cost attribution (per team/project)
  - Compliance reporting

Usage:
    from examples.simulation import run_scenario, SCENARIOS
    events = list(run_scenario("code-review", template="enterprise"))
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Tuple

from agentmesh import AgentMesh
from agentmesh.attribution.chargebacks import CostAttributor
from agentmesh.compliance.reporter import ComplianceReporter
from agentmesh.events.bus import get_bus, GovernanceEvent
from agentmesh.policy.engine import Policy
from agentmesh.templates import load_template

# ── Simulated LLM Responses ──────────────────────────────────────────────────

CODE_REVIEW_RESPONSES = [
    "I've analyzed the changes. The authentication middleware looks solid. "
    "Found 2 issues: (1) Missing rate limiting on /login endpoint — recommend "
    "adding Token Bucket algorithm. (2) JWT expiry hardcoded to 24h — should be "
    "configurable. Overall: LGTM with minor fixes required.",

    "Security scan complete. No SQL injection vulnerabilities detected. "
    "One concern: user input passed to os.path.join() on line 147 — potential "
    "path traversal. Recommend using pathlib.Path instead. "
    "CVSS score: 4.3 (Medium). Patch before merge.",

    "Performance analysis: The N+1 query pattern in UserRepository.get_orders() "
    "will cause degradation at scale. Switching to a JOIN query would reduce "
    "database round-trips from O(n) to O(1). Estimated 85% latency reduction "
    "under load. Recommend addressing before production.",

    "Code quality review: Test coverage is 67%, below the 80% threshold. "
    "Missing unit tests for the error handling paths in PaymentProcessor. "
    "Docs are comprehensive. Naming conventions followed correctly. "
    "Minor: 3 TODO comments should be tracked in JIRA, not left in code.",

    "Architecture review: The new microservice introduces a circular dependency "
    "with the notification service. Recommend extracting shared types into a "
    "common library. The event-driven pattern chosen is correct for this use case. "
    "Consider adding a circuit breaker for the downstream payment API calls.",
]

CUSTOMER_SERVICE_RESPONSES = [
    "Based on the customer's account history, I've identified their issue: "
    "the subscription was charged twice due to a race condition in the billing "
    "system. Recommending a full refund of $49.99. Escalation not required. "
    "Resolution time: immediate.",

    "Customer sentiment: Frustrated (confidence: 0.89). Root cause: delayed "
    "shipping from warehouse. Order #847291 shows carrier scan at Memphis hub "
    "3 days ago with no movement. Recommend proactive outreach and 20% discount "
    "on next order as goodwill gesture.",

    "FAQ match found (similarity: 0.94): This is a known issue with the "
    "iOS 17.3 update affecting push notifications. Standard resolution: "
    "Settings → Notifications → Reset Permissions. If unresolved, escalate "
    "to Tier 2. Average resolution rate: 91%.",
]

RESEARCH_RESPONSES = [
    "Analysis of the dataset reveals a statistically significant correlation "
    "(r=0.73, p<0.001) between feature X and the target variable. "
    "Recommend including in the model. Running feature importance analysis "
    "to confirm before adding to production pipeline.",

    "Literature review complete. Found 14 relevant papers from 2023-2026. "
    "Key finding: transformer-based approaches outperform traditional methods "
    "by 23% on this task type. Recommending fine-tuning approach over "
    "training from scratch given our dataset size (n=45,000).",
]

SCENARIO_RESPONSES = {
    "code-review": CODE_REVIEW_RESPONSES,
    "customer-service": CUSTOMER_SERVICE_RESPONSES,
    "research": RESEARCH_RESPONSES,
}

# ── Simulated PRs / Tasks ─────────────────────────────────────────────────────

SCENARIO_TASKS = {
    "code-review": [
        {"id": "PR-142", "title": "Add user authentication middleware",       "files": 5,  "complexity": 0.65, "team": "engineering", "project": "auth-service"},
        {"id": "PR-143", "title": "Refactor payment processing service",      "files": 12, "complexity": 0.93, "team": "payments",    "project": "billing"},
        {"id": "PR-144", "title": "Fix typo in README",                       "files": 1,  "complexity": 0.04, "team": "engineering", "project": "docs"},
        {"id": "PR-145", "title": "Implement caching layer for product API",  "files": 8,  "complexity": 0.72, "team": "backend",     "project": "product-api"},
        {"id": "PR-146", "title": "Security patches for dependency upgrades", "files": 3,  "complexity": 0.88, "team": "security",    "project": "infra"},
        {"id": "PR-147", "title": "Add analytics event tracking",             "files": 6,  "complexity": 0.55, "team": "growth",      "project": "analytics"},
    ],
    "customer-service": [
        {"id": "TKT-901", "title": "Double billing issue",         "complexity": 0.60, "team": "cx", "project": "billing-support"},
        {"id": "TKT-902", "title": "Shipping delay complaint",     "complexity": 0.40, "team": "cx", "project": "logistics-support"},
        {"id": "TKT-903", "title": "Push notification broken",     "complexity": 0.30, "team": "cx", "project": "mobile-support"},
        {"id": "TKT-904", "title": "Account access issue",         "complexity": 0.55, "team": "cx", "project": "account-support"},
        {"id": "TKT-905", "title": "Refund request for cancelled", "complexity": 0.45, "team": "cx", "project": "billing-support"},
    ],
    "research": [
        {"id": "EXP-01", "title": "Feature correlation analysis",   "complexity": 0.85, "team": "ml-research", "project": "model-v3"},
        {"id": "EXP-02", "title": "Literature review: transformers","complexity": 0.90, "team": "ml-research", "project": "model-v3"},
        {"id": "EXP-03", "title": "Baseline model evaluation",      "complexity": 0.75, "team": "ml-research", "project": "evaluation"},
    ],
}

# Agent steps per task (simulates the ReAct loop)
STEPS_PER_TASK = {
    "code-review":      ["understand_diff", "analyze_code",  "security_scan", "generate_report"],
    "customer-service": ["classify_issue",  "lookup_account","resolve",       "log_resolution"],
    "research":         ["search_papers",   "analyze_data",  "synthesize",    "write_summary"],
}

# ── Event types (yielded to dashboard) ───────────────────────────────────────

@dataclass
class SimEvent:
    kind: str               # "step"|"cache_hit"|"model_route"|"budget"|"circuit"|"complete"|"error"|"quota_warn"|"quota_block"|"escalation"|"vendor_route"
    task_id: str = ""
    task_title: str = ""
    step: str = ""
    message: str = ""
    model: str = ""
    vendor: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    cache_similarity: float = 0.0
    from_model: str = ""
    to_model: str = ""
    iteration: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    budget_pct: float = 1.0
    audit_entries: int = 0
    # Quota fields
    quota_used: int = 0
    quota_limit: int = 0
    quota_pct: float = 0.0
    escalation_id: str = ""
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


# ── Mock LLM ─────────────────────────────────────────────────────────────────

def _mock_llm_call(kwargs: Dict[str, Any], scenario: str = "code-review") -> Any:
    """
    Simulates an LLM response with realistic token counts and latency.
    Returns an object that AgentMesh's token extraction understands.
    """
    messages = kwargs.get("messages", [])
    input_chars = sum(len(str(m.get("content", ""))) for m in messages)
    input_tokens = max(100, input_chars // 4 + random.randint(-20, 50))

    # Output length varies by model tier
    model = kwargs.get("model", "claude-haiku-4-5")
    if "opus" in model.lower():
        output_tokens = random.randint(300, 600)
    elif "sonnet" in model.lower():
        output_tokens = random.randint(200, 400)
    else:
        output_tokens = random.randint(80, 200)

    # Simulate network latency
    time.sleep(random.uniform(0.05, 0.15))

    responses = SCENARIO_RESPONSES.get(scenario, CODE_REVIEW_RESPONSES)
    content = random.choice(responses)

    class MockUsage:
        pass

    class MockResponse:
        pass

    usage = MockUsage()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    resp = MockResponse()
    resp.usage = usage
    resp.model = model
    resp.content = [type("Block", (), {"text": content})()]
    return resp


# ── Scenario Runner ───────────────────────────────────────────────────────────

def run_scenario(
    scenario: str = "code-review",
    template: str = "enterprise",
    trip_circuit_breaker: bool = False,
    enable_quota: bool = True,
    trip_quota: bool = False,
    vendors: Optional[List[str]] = None,
) -> Generator[SimEvent, None, None]:
    """
    Run a simulated enterprise agentic workflow with AgentMesh governance.

    Yields SimEvent objects as each governance layer fires, suitable for
    driving both terminal and web dashboards.

    Args:
        scenario: "code-review" | "customer-service" | "research"
        template: policy template name — "enterprise", "fintech", "healthcare", "research"
        trip_circuit_breaker: if True, runs enough iterations to trip the circuit breaker
        enable_quota: if True, attach quota enforcement (demo team budgets)
        trip_quota: if True, set tight quota so one team gets blocked (triggers escalation)
        vendors: list of vendors for multi-vendor routing demo e.g. ["anthropic","openai","google"]
    """
    from agentmesh.quota.engine import QuotaPolicy, QuotaEnforcer, QuotaIdentity, QuotaStatus
    from agentmesh.quota.escalation import EscalationManager
    from agentmesh.optimizer.multi_vendor import MultiVendorRouter

    policy_yaml = load_template(template)
    policy = Policy.from_yaml(policy_yaml)

    # For demo: use tighter limits so governance fires visibly
    demo_policy = Policy.from_dict({
        "name": f"{template}-demo",
        "budget": {
            "per_run_tokens": 60_000,
            "daily_tokens": 200_000,
            "monthly_usd": 500,
            "hard_stop": True,
        },
        "model_routing": {
            "default": "claude-haiku-4-5",
            "upgrade_triggers": [
                {"condition": "task_complexity > 0.80", "model": "claude-sonnet-4-6"},
                {"condition": "requires_reasoning",     "model": "claude-sonnet-4-6"},
            ],
            "max_allowed": "claude-sonnet-4-6",
        },
        "optimization": {
            "semantic_cache": True,
            "compression_threshold": 0.50,
            "context_pruning": True,
            "cache_ttl_seconds": 3600,
        },
        "circuit_breaker": {
            "max_iterations": 5 if trip_circuit_breaker else 30,
            "max_tool_calls": 100,
            "stall_detection_seconds": 300,
        },
        "compliance": {
            "frameworks": ["eu-ai-act", "nist-ai-rmf", "soc2"],
            "pii_detection": False,
        },
    })

    # ── Quota setup ───────────────────────────────────────────────────────
    quota_policy = None
    quota_enforcer = None
    escalation_mgr = None
    if enable_quota:
        # Demo quotas: payments team is tight (will trigger warning/block if trip_quota)
        payments_limit = 500 if trip_quota else 200_000
        quota_policy = QuotaPolicy(
            global_monthly_tokens=5_000_000,
            team_monthly_tokens={
                "engineering": 1_000_000,
                "payments":    payments_limit,
                "backend":     500_000,
                "security":    300_000,
                "growth":      200_000,
                "cx":          400_000,
                "ml-research": 800_000,
            },
            tool_monthly_tokens={
                "vscode-copilot":  500_000,
                "github-ci":       200_000,
                "teams-bot":       300_000,
                "excel-ai":        100_000,
            },
            warn_at_pct=0.70,
            hard_stop_at_pct=1.00,
            auto_escalate=True,
            temp_grant_tokens=50_000,
        )
        quota_enforcer = QuotaEnforcer(quota_policy)
        escalation_mgr = EscalationManager(
            enforcer=quota_enforcer,
            auto_temp_grant=True,
        )
        # Pre-seed realistic usage so quota warnings/blocks fire in demo
        if trip_quota:
            # Payments team is already at the limit (simulate prior usage this month)
            quota_enforcer.store.set("team", "payments", "monthly", 501)
        else:
            # Normal scenario: pre-seed some usage so warnings fire at 70%+
            quota_enforcer.store.set("team", "engineering", "monthly", 720_000)  # 72%
            quota_enforcer.store.set("team", "security",    "monthly", 225_000)  # 75%

    # ── Multi-vendor router ───────────────────────────────────────────────
    mv_router = None
    active_vendors = vendors or ["anthropic"]
    if len(active_vendors) > 1:
        mv_router = MultiVendorRouter(
            vendors=active_vendors,
            routing_strategy="cheapest_capable",
        )

    mesh = AgentMesh(policy=demo_policy)
    attributor = CostAttributor()
    tasks = SCENARIO_TASKS.get(scenario, SCENARIO_TASKS["code-review"])
    steps = STEPS_PER_TASK.get(scenario, STEPS_PER_TASK["code-review"])

    # ── System prompt (repeated each call → triggers compression) ────────────
    SYSTEM_PROMPT = (
        f"You are an expert {scenario.replace('-', ' ')} agent for an enterprise software company. "
        "You analyze tasks carefully and provide structured, actionable output. "
        "Always consider security, performance, and maintainability. "
        "Format your responses with clear sections and specific recommendations. "
        "Reference relevant best practices and industry standards. "
        "You have access to tools: read_file, web_search, run_tests, check_security. "
        "Use them judiciously — prefer reading existing context before searching the web."
    )

    global_iteration = 0

    for task in tasks:
        mesh.reset()
        task_id = task["id"]
        task_title = task["title"]
        task_complexity = task.get("complexity", 0.5)
        team = task.get("team", "default")
        project = task.get("project", "default")

        # Build a conversation-style context (grows with each step → tests compression)
        conversation: list = [{"role": "system", "content": SYSTEM_PROMPT}]

        for step_idx, step_name in enumerate(steps):
            global_iteration += 1

            # Add user message for this step
            user_msg = _build_step_message(task, step_name, step_idx, scenario)
            conversation.append({"role": "user", "content": user_msg})

            kwargs = {
                "model": "claude-haiku-4-5",  # default; router may upgrade
                "messages": list(conversation),
                "task_complexity": task_complexity,
            }

            # Track pre-call state for event emission
            pre_tokens = mesh.budget.tokens_used
            pre_cache_hits = mesh.cache.hits if mesh.cache else 0
            pre_model = kwargs["model"]

            # ── Pre-call governance ──────────────────────────────────────────
            from agentmesh.budget.enforcer import BudgetExceededError
            from agentmesh.optimizer.circuit_breaker import CircuitBreakerError
            from agentmesh.quota.engine import QuotaIdentity, QuotaStatus, QuotaExceededError

            # ── Quota check ──────────────────────────────────────────────────
            # Determine which tool this "call" is coming from (simulate variety)
            tool_for_step = {
                "understand_diff": "vscode-copilot",
                "analyze_code":    "github-ci",
                "security_scan":   "github-ci",
                "generate_report": "teams-bot",
            }.get(step_name, "unknown")

            identity = QuotaIdentity(
                user=f"dev-{team}@company.com",
                team=team,
                project=project,
                tool=tool_for_step,
            )

            if quota_enforcer:
                q_result = quota_enforcer.check(identity)
                if q_result.status == QuotaStatus.BLOCK:
                    # Auto-escalate and yield escalation event
                    esc_req = None
                    if escalation_mgr:
                        esc_req = escalation_mgr.request(
                            identity=identity,
                            quota_result=q_result,
                            requested_tokens=500_000,
                            reason=f"Sprint quota exhausted for team {team} on tool {tool_for_step}",
                            priority="high",
                        )
                    _esc_id = esc_req.id if esc_req else ""
                    get_bus().emit(GovernanceEvent(
                        kind="quota_block", team=team, user=identity.user, tool=tool_for_step,
                        quota_pct=q_result.pct_used, quota_used=q_result.used_tokens,
                        quota_limit=q_result.limit_tokens, escalation_id=_esc_id,
                        message=q_result.message,
                    ))
                    if _esc_id:
                        get_bus().emit(GovernanceEvent(
                            kind="escalation", team=team, user=identity.user, tool=tool_for_step,
                            escalation_id=_esc_id, quota_pct=q_result.pct_used,
                            message=f"Auto-filed {_esc_id} — temp grant applied",
                        ))
                    yield SimEvent(
                        kind="escalation",
                        task_id=task_id, task_title=task_title, step=step_name,
                        message=f"Quota BLOCKED: {q_result.message} — escalation filed",
                        quota_used=q_result.used_tokens, quota_limit=q_result.limit_tokens,
                        quota_pct=q_result.pct_used,
                        escalation_id=_esc_id,
                        iteration=global_iteration,
                        total_tokens=mesh.budget.tokens_used, total_cost=mesh.budget.cost_usd,
                        budget_pct=mesh.budget.remaining_ratio(), audit_entries=len(mesh.audit.entries),
                    )
                    # Temporary grant applied by escalation_mgr — continue after warning
                elif q_result.status == QuotaStatus.WARN:
                    get_bus().emit(GovernanceEvent(
                        kind="quota_warn", team=team, user=identity.user, tool=tool_for_step,
                        quota_pct=q_result.pct_used, quota_used=q_result.used_tokens,
                        quota_limit=q_result.limit_tokens, message=q_result.message,
                    ))
                    yield SimEvent(
                        kind="quota_warn",
                        task_id=task_id, task_title=task_title, step=step_name,
                        message=q_result.message,
                        quota_used=q_result.used_tokens, quota_limit=q_result.limit_tokens,
                        quota_pct=q_result.pct_used,
                        iteration=global_iteration,
                        total_tokens=mesh.budget.tokens_used, total_cost=mesh.budget.cost_usd,
                        budget_pct=mesh.budget.remaining_ratio(), audit_entries=len(mesh.audit.entries),
                    )

            # ── Multi-vendor routing ──────────────────────────────────────────
            vendor_decision = None
            selected_vendor = "anthropic"
            if mv_router:
                user_msg_for_routing = next(
                    (m["content"] for m in reversed(kwargs.get("messages", [])) if m.get("role") == "user"), ""
                )
                vendor_decision = mv_router.route(user_msg_for_routing)
                selected_vendor = vendor_decision.vendor
                kwargs["model"] = vendor_decision.model
                if vendor_decision.vendor != "anthropic":
                    get_bus().emit(GovernanceEvent(
                        kind="vendor_route", team=team, tool=tool_for_step,
                        vendor=vendor_decision.vendor, model=vendor_decision.model,
                        complexity_score=vendor_decision.complexity_score,
                        cost_usd=vendor_decision.estimated_cost,
                        message=f"{vendor_decision.tier.value} | score={vendor_decision.complexity_score:.2f} | est=${vendor_decision.estimated_cost:.5f}",
                    ))
                    yield SimEvent(
                        kind="vendor_route",
                        task_id=task_id, task_title=task_title, step=step_name,
                        vendor=vendor_decision.vendor,
                        model=vendor_decision.model,
                        message=(
                            f"Vendor: {vendor_decision.vendor} | Model: {vendor_decision.model} | "
                            f"Tier: {vendor_decision.tier.value} | Score: {vendor_decision.complexity_score:.2f} | "
                            f"Est: ${vendor_decision.estimated_cost:.5f}"
                        ),
                        iteration=global_iteration,
                        total_tokens=mesh.budget.tokens_used, total_cost=mesh.budget.cost_usd,
                        budget_pct=mesh.budget.remaining_ratio(), audit_entries=len(mesh.audit.entries),
                    )

            try:
                mesh.circuit_breaker.check()
                mesh.budget.check_pre_call(kwargs)
            except CircuitBreakerError as e:
                yield SimEvent(
                    kind="circuit",
                    task_id=task_id,
                    task_title=task_title,
                    step=step_name,
                    message=str(e),
                    iteration=global_iteration,
                    total_tokens=mesh.budget.tokens_used,
                    total_cost=mesh.budget.cost_usd,
                    budget_pct=mesh.budget.remaining_ratio(),
                    audit_entries=len(mesh.audit.entries),
                )
                return
            except BudgetExceededError as e:
                yield SimEvent(
                    kind="error",
                    task_id=task_id,
                    message=str(e),
                    total_tokens=mesh.budget.tokens_used,
                    total_cost=mesh.budget.cost_usd,
                )
                return

            # ── Cache lookup — key on last user message only (more discriminating)
            cache_hit = False
            cache_sim = 0.0
            cached_response = None
            cache_key = ""
            if mesh.cache:
                messages_list = kwargs.get("messages", [])
                user_msgs = [m for m in messages_list if m.get("role") == "user"]
                cache_key = user_msgs[-1]["content"] if user_msgs else str(messages_list)
                # Probe similarity score for display (before get() eviction changes entries)
                if mesh.cache._entries:
                    normalized = mesh.cache._normalize(cache_key)
                    embedding  = mesh.cache._embedder(normalized)
                    score, _   = mesh.cache._find_best(embedding)
                    if score >= mesh.cache.similarity_threshold:
                        cache_sim = round(score, 3)
                # Always call get() so both hit AND miss counters increment properly
                cached_response = mesh.cache.get(cache_key)
                if cached_response is not None:
                    cache_hit = True
                    get_bus().emit(GovernanceEvent(
                        kind="cache_hit", team=team, tool=tool_for_step,
                        model=kwargs.get("model", ""), cache_layer="semantic",
                        similarity=cache_sim,
                        message=f"similarity={cache_sim:.3f} | 0 tokens burned",
                    ))
                else:
                    get_bus().emit(GovernanceEvent(
                        kind="cache_miss", team=team, tool=tool_for_step,
                        model=kwargs.get("model", ""), cache_layer="miss",
                    ))

            # ── Model routing ────────────────────────────────────────────────
            if mesh.router:
                routed = mesh.router.route(dict(kwargs))
                new_model = routed.get("model", kwargs["model"])
                if new_model != pre_model:
                    kwargs["model"] = new_model
                    yield SimEvent(
                        kind="model_route",
                        task_id=task_id,
                        task_title=task_title,
                        step=step_name,
                        from_model=pre_model,
                        to_model=new_model,
                        message=f"Routed {pre_model} → {new_model} (complexity={task_complexity:.2f})",
                        iteration=global_iteration,
                        total_tokens=mesh.budget.tokens_used,
                        total_cost=mesh.budget.cost_usd,
                        budget_pct=mesh.budget.remaining_ratio(),
                        audit_entries=len(mesh.audit.entries),
                    )

            # ── Compression ──────────────────────────────────────────────────
            if mesh.compressor:
                remaining = mesh.budget.remaining_ratio()
                threshold = demo_policy.schema.optimization.compression_threshold
                if remaining < threshold:
                    pre_len = len(kwargs.get("messages", []))
                    kwargs = mesh.compressor.maybe_compress(kwargs, remaining)
                    post_len = len(kwargs.get("messages", []))
                    if post_len < pre_len:
                        yield SimEvent(
                            kind="budget",
                            task_id=task_id,
                            step=step_name,
                            message=f"Prompt compressed: {pre_len}→{post_len} messages (budget {remaining:.0%} remaining)",
                            budget_pct=remaining,
                            total_tokens=mesh.budget.tokens_used,
                            total_cost=mesh.budget.cost_usd,
                            audit_entries=len(mesh.audit.entries),
                        )

            # ── Audit + LLM call (skip if cache hit) ─────────────────────────
            mesh.audit.record_call(kwargs, agent_id=f"agent-{team}")

            if cache_hit and cached_response is not None:
                # Serve from cache — no LLM call, no tokens burned
                response = cached_response
                tokens_in  = 0
                tokens_out = 0
                cost_this_call = 0.0
            else:
                # ── Mock LLM call ────────────────────────────────────────────
                response = _mock_llm_call(kwargs, scenario)
                tokens_in  = response.usage.input_tokens
                tokens_out = response.usage.output_tokens
                cost_this_call = (tokens_in / 1_000_000) * _model_cost(kwargs["model"])

                # Emit LLM call event to real-time bus
                get_bus().emit(GovernanceEvent(
                    kind="llm_call", team=team, user=identity.user, tool=tool_for_step,
                    model=kwargs["model"], vendor=selected_vendor,
                    tokens_used=tokens_in + tokens_out, cost_usd=cost_this_call,
                    message=f"{tokens_in}in + {tokens_out}out = {tokens_in + tokens_out} tokens | ${cost_this_call:.5f}",
                ))

                # Post-call governance
                mesh.budget.record_usage(response)

                # Store in cache for future hits (key = last user message)
                if mesh.cache:
                    mesh.cache.put(
                        cache_key,
                        response,
                        model=kwargs["model"],
                        tokens=tokens_in + tokens_out,
                    )

            mesh.audit.record_result(response)
            mesh.circuit_breaker.increment()

            # ── Deduct from quota ─────────────────────────────────────────────
            if quota_enforcer and tokens_in + tokens_out > 0:
                quota_enforcer.consume(identity, tokens_in + tokens_out)

            # ── Record attribution ────────────────────────────────────────────
            attributor.record(
                model=kwargs["model"],
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=cost_this_call,
                team=team,
                project=project,
            )

            # ── Add assistant response to conversation ───────────────────────
            answer = response.content[0].text if response.content else "Analysis complete."
            conversation.append({"role": "assistant", "content": answer[:300]})

            # ── Yield step event ──────────────────────────────────────────────
            yield SimEvent(
                kind="cache_hit" if cache_hit else "step",
                task_id=task_id,
                task_title=task_title,
                step=step_name,
                message=answer[:120] + "…" if len(answer) > 120 else answer,
                model=kwargs["model"],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_this_call,
                cache_hit=cache_hit,
                cache_similarity=cache_sim,
                iteration=global_iteration,
                total_tokens=mesh.budget.tokens_used,
                total_cost=mesh.budget.cost_usd,
                budget_pct=mesh.budget.remaining_ratio(),
                audit_entries=len(mesh.audit.entries),
            )

    # ── Final summary event ───────────────────────────────────────────────────
    reporter = ComplianceReporter(mesh=mesh)
    compliance = reporter.generate(framework="eu-ai-act")

    # Quota snapshot
    quota_snapshot = []
    if quota_enforcer and quota_policy:
        for team_name, limit in quota_policy.team_monthly_tokens.items():
            used = quota_enforcer.store.get("team", team_name, "monthly")
            quota_snapshot.append({
                "dimension": "team", "key": team_name,
                "used": used, "limit": limit,
                "pct_used": round(used / limit, 4) if limit else 0,
                "remaining": max(0, limit - used),
            })

    # Vendor comparison table
    vendor_comparison = []
    if mv_router:
        vendor_comparison = mv_router.cost_comparison(input_tokens=1000, output_tokens=300)
    else:
        from agentmesh.optimizer.multi_vendor import MultiVendorRouter as MVR
        vendor_comparison = MVR(vendors=["anthropic", "openai", "google"]).cost_comparison(
            input_tokens=1000, output_tokens=300
        )

    yield SimEvent(
        kind="complete",
        message="Scenario complete",
        total_tokens=mesh.budget.tokens_used,
        total_cost=mesh.budget.cost_usd,
        budget_pct=mesh.budget.remaining_ratio(),
        audit_entries=len(mesh.audit.entries),
        data={
            "mesh_stats": mesh.stats,
            "attribution": attributor.summary(group_by="team").to_dict() if attributor.total_calls else [],
            "attribution_by_project": attributor.summary(group_by="project").to_dict() if attributor.total_calls else [],
            "audit_entries": [
                {
                    "entry_id": e.entry_id[:8],
                    "timestamp": time.strftime("%H:%M:%S", time.localtime(e.timestamp)),
                    "event_type": e.event_type,
                    "agent_id": e.agent_id or "-",
                    "model": e.model or "-",
                    "tokens_used": e.tokens_used,
                }
                for e in mesh.audit.entries
            ],
            "compliance": compliance.to_dict(),
            "cache_stats": mesh.cache.stats if mesh.cache else {},
            "circuit_breaker": {
                "iterations": mesh.circuit_breaker.iteration_count,
                "tool_calls": mesh.circuit_breaker.tool_call_count,
                "tripped": False,
            },
            "quota_snapshot": quota_snapshot,
            "escalations": escalation_mgr.summary() if escalation_mgr else {"total": 0, "pending": 0, "requests": []},
            "vendor_comparison": vendor_comparison,
            "active_vendors": active_vendors,
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_step_message(task: Dict, step: str, idx: int, scenario: str) -> str:
    title = task["title"]
    complexity = task.get("complexity", 0.5)
    files = task.get("files", 3)
    msgs = {
        "code-review": {
            "understand_diff": f"I need you to review {title}. This PR modifies {files} files with complexity score {complexity:.2f}. Start by understanding the scope of changes.",
            "analyze_code": f"Perform a detailed code quality and logic analysis of the changes in {title}. Check for anti-patterns, error handling, and test coverage.",
            "security_scan": f"Run a security review of {title}. Check for OWASP Top 10 vulnerabilities, injection risks, authentication issues, and data exposure.",
            "generate_report": f"Generate the final review report for {title}. Include a go/no-go recommendation, severity of issues, and required changes before merge.",
        },
        "customer-service": {
            "classify_issue":   f"Classify this customer ticket: '{title}'. Determine issue type, urgency, and initial routing.",
            "lookup_account":   f"Look up account details for the customer reporting: '{title}'. Check subscription status and recent activity.",
            "resolve":          f"Determine the optimal resolution for: '{title}'. Consider standard procedures and customer tier.",
            "log_resolution":   f"Log the resolution for ticket '{title}'. Include actions taken, outcome, and follow-up required.",
        },
        "research": {
            "search_papers":  f"Search for relevant literature on: '{title}'. Focus on 2023-2026 publications.",
            "analyze_data":   f"Analyze the dataset for experiment: '{title}'. Run statistical analysis and identify patterns.",
            "synthesize":     f"Synthesize findings from the analysis of '{title}'. Draw conclusions and recommendations.",
            "write_summary":  f"Write the research summary for '{title}'. Include methodology, findings, and next steps.",
        },
    }
    step_msgs = msgs.get(scenario, msgs["code-review"])
    return step_msgs.get(step, f"Execute step '{step}' for task: {title}")


def _model_cost(model: str) -> float:
    costs = {
        "claude-haiku-4-5": 0.80,
        "claude-sonnet-4-6": 3.00,
        "claude-opus-4-8": 15.00,
        "gpt-4o-mini": 0.15,
        "gpt-4o": 2.50,
    }
    for k, v in costs.items():
        if k in model:
            return v
    return 3.0


SCENARIOS = list(SCENARIO_TASKS.keys())
TEMPLATES = ["enterprise", "fintech", "healthcare", "research", "customer_service"]
