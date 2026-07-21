# Changelog

All notable changes to AgentMesh are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
AgentMesh uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Chrome Extension [0.2.3] — 2026-07-10

### Fixed
- **Send no longer blocks or requires a second click.** The content script
  used to `preventDefault()` every Send/Enter, gate it behind a proxy round
  trip, and require the user to click "Send as-is" (or wait out a 4s timer)
  before the message actually went anywhere — on every single prompt.
  Governance feedback is now fire-and-forget: the real send fires
  immediately and normally, and an informational toast (cache hit,
  compression available, quota warning) appears afterward only when there's
  something worth surfacing. Nothing is ever gated on it.
- **The "governance check" was a real LLM call, not a dry run.** The
  background service worker's comments claimed `X-AgentMesh-Dry-Run: true`
  was sent with the pre-check request; the header was never actually set,
  so every check silently triggered a full vendor-routed generation instead
  of the instant preview path. This was the dominant source of the
  multi-second delay before a message would send.
- Removed the "Send Optimized" swap-in feature — it was parsing the
  dry-run preview text as if it were usable prompt content and offering to
  substitute it into the chat box, which was never safe.

---

## [0.2.0] — 2026-06-12

### Added

**New Integrations**
- AutoGen v2 / AG2 (`agentmesh.integrations.autogen`) — Microsoft's multi-agent framework
- Haystack 2.x (`agentmesh.integrations.haystack`) — deepset pipeline governance
- Google ADK / Vertex AI (`agentmesh.integrations.google_adk`) — Gemini agent governance
- NVIDIA NIM (`agentmesh.integrations.nvidia_nim`) — NIM-compatible OpenAI client proxy
- `AgentMesh.wrap_autogen()`, `wrap_haystack()` added to core

**Semantic Caching**
- `agentmesh.cache.SemanticCache` — zero-dependency in-memory cosine-similarity cache
- Pure-Python n-gram embeddings; swap in real embedder via `embedder=` parameter
- Cache stats: `hit_rate`, `tokens_saved`, `cost_saved_usd`
- Integrated into `AgentMesh.intercept()` and `intercept_async()`

**Async Support**
- `AgentMesh.intercept_async()` — full async governance pipeline
- All governance layers (budget, circuit breaker, router, compressor, cache) work async

**Cost Attribution & Chargeback**
- `agentmesh.attribution.CostAttributor` — record and aggregate AI spend
- Group by team, project, workflow, user, or model
- `summary()` → `UsageSummaryCollection` with `.to_json()`, `.to_csv()`
- `budget_status()` — compare spend vs. budgets per team
- `top_spenders()` — ranked list of highest-spending groups

**Compliance Reports**
- `agentmesh.compliance.ComplianceReporter` — automated compliance evaluation
- Frameworks: `eu-ai-act`, `nist-ai-rmf`, `hipaa`, `soc2`, `iso-42001`
- `generate()` → `ComplianceReport` with pass/fail per requirement + gap remediation
- `generate_all()` → all frameworks in one call
- `report.save()` → evidence JSON for auditors

**CLI**
- `agentmesh validate <policy.yaml>` — validate policy files
- `agentmesh audit view <file>` — inspect audit trails (table or JSON)
- `agentmesh audit verify <file>` — verify chain integrity
- `agentmesh compliance report --framework eu-ai-act` — generate compliance reports
- `agentmesh proxy --port 8080` — start governance HTTP proxy
- `agentmesh benchmark` — estimate cost savings

**HTTP Proxy Mode**
- `agentmesh.proxy.AgentMeshProxy` — drop-in OpenAI-compatible HTTP proxy
- Zero code changes: point any LLM SDK at `http://localhost:8080`

**Enterprise Policy Templates**
- `agentmesh.templates.load_template(name)` — pre-built YAML policies
- Templates: `fintech` (SOX/PCI-DSS), `healthcare` (HIPAA), `enterprise`, `research`, `customer_service`, `nvidia_nim`

**HuggingFace Space**
- Interactive cost savings calculator at `spaces/app.py`
- Deployed at `huggingface.co/spaces/anilatambharii/agentmesh`

### Changed

- `AgentMesh.stats` now includes `model_downgrades`, `tool_calls`, and `cache` sub-dict
- `AgentMesh.reset()` method added for per-run state reset
- `AgentMesh.__init__` now accepts `cache_similarity_threshold` in config

### Fixed

- `agentmesh/integrations/pydantic_ai.py` — was referenced in core but missing; now implemented
- `agentmesh/proxy/` — was empty; `server.py` now fully implemented

---

## [0.1.0] — 2026-05-15

### Added

**Core Governance**
- `AgentMesh` — central governance proxy
- `Policy` / `PolicySchema` — Pydantic-validated YAML policy definitions
- `BudgetEnforcer` — token budget tracking and hard stop enforcement
- `CircuitBreaker` — runaway loop prevention (max iterations, tool calls, stall detection)
- `AuditTrail` — Ed25519-signed append-only audit chain

**Optimization**
- `ModelRouter` — dynamic model selection based on task complexity and budget
- `PromptCompressor` — heuristic context pruner + LLMLingua integration

**Framework Integrations**
- LangGraph — callback handler interception
- CrewAI — Crew wrapper
- OpenAI Agents SDK — agent run() wrapper

**Infrastructure**
- Apache 2.0 license
- `pyproject.toml` with Hatchling build backend
- GitHub Actions CI (Python 3.10/3.11/3.12, ruff, black, mypy, bandit, CodeQL)
- 11 unit tests covering core functionality
