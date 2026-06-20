# AgentMesh

**The governance proxy for every AI tool your team uses.**

> *"Istio for AI — intercept, cache, and govern every LLM call across Claude Code, VS Code Copilot, ChatGPT, Gemini, and your own agents. One proxy, one policy, one bill."*

[![CI](https://github.com/anilatambharii/agentmesh/actions/workflows/ci.yml/badge.svg)](https://github.com/anilatambharii/agentmesh/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/agentmesh-proxy.svg)](https://badge.fury.io/py/agentmesh-proxy)
[![PyPI Downloads](https://static.pepy.tech/badge/agentmesh-proxy)](https://pepy.tech/project/agentmesh-proxy)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

![AgentMesh demo — 85% cache hit rate, 75% cost reduction](docs/demo.gif)

---

## Enterprise Governance Features

AgentMesh ships a complete enterprise security and compliance stack — no third-party SaaS required.

| Feature | Module | What it does |
|---|---|---|
| **PII / PHI / PCI masking** | `agentmesh/security/pii_scanner.py` | Scans every prompt for SSN, credit cards, medical records, AWS keys, JWTs — masks or blocks before the LLM sees them |
| **Prompt injection detection** | `agentmesh/security/injection_detector.py` | 14 rules covering DAN, roleplay jailbreaks, role confusion, encoding tricks — HIGH risk blocked automatically |
| **Output toxicity filter** | `agentmesh/security/toxicity_filter.py` | Post-call scan of LLM responses for hate speech, hallucinations, policy leaks, refusal bypasses |
| **Cost anomaly detection** | `agentmesh/monitoring/anomaly_detector.py` | Sliding-window burn rate, spend spike, runaway agent loop, cache miss flood — fires alerts in real time |
| **Slack / PagerDuty alerts** | `agentmesh/integrations/webhooks.py` | Fire-and-forget alerts on anomalies, quota blocks, injection detections — never blocks the request path |
| **Redis distributed cache** | `agentmesh/cache/redis_backend.py` | Shared semantic cache across multiple proxy instances — falls back to in-memory if Redis is unavailable |
| **SAML / SSO identity** | `agentmesh/integrations/saml_handler.py` | Extracts team/user identity from SAML assertions, OIDC JWTs, or pre-verified proxy headers |
| **Vendor health monitor** | `agentmesh/optimizer/health_monitor.py` | Per-vendor circuit breaker — automatically routes around degraded APIs |
| **EU AI Act / HIPAA reports** | `agentmesh/compliance/pdf_report.py` | One-click compliance reports for EU AI Act, HIPAA, SOC2, NIST AI RMF — Markdown and PDF |
| **Chargeback export** | `agentmesh/attribution/chargebacks.py` | Per-team, per-month, per-model cost attribution — CSV and JSON for internal billing |

### Quick config

```python
from agentmesh.proxy.server import ProxyConfig, build_proxy_app

app = build_proxy_app(ProxyConfig(
    vendors=["anthropic", "openai", "google"],

    # Security
    pii_mode="mask",               # "mask" | "redact" | "block"
    block_injections=True,         # block HIGH-risk prompt injection
    toxicity_filter=True,          # filter harmful LLM output

    # Monitoring
    anomaly_detection=True,
    slack_webhook="https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK",
    pagerduty_key="YOUR_PD_ROUTING_KEY",

    # Infrastructure
    redis_url="redis://your-redis:6379/0",   # distributed cache
    sso_enabled=True,                        # JWT/SAML identity extraction

    # Deterministic mode — temperature=0 per team
    deterministic_teams={"healthcare": "claude-haiku-4-5", "legal": "claude-sonnet-4-6"},
))
```

New governance response headers:

```
X-AgentMesh-PII-Findings:     3           # entities masked in this prompt
X-AgentMesh-PII-Types:        EMAIL,SSN   # types detected
X-AgentMesh-Injection-Risk:   high        # injection detected (request blocked)
X-AgentMesh-Toxicity:         TOXICITY    # output toxicity type
X-AgentMesh-Toxicity-Action:  redacted    # redacted | blocked
X-AgentMesh-Anomaly:          RUNAWAY_LOOP
X-AgentMesh-SSO-Source:       jwt         # jwt | saml | header
X-AgentMesh-Deterministic:    true
```

### Compliance report (one line)

```python
from agentmesh.compliance.pdf_report import ComplianceReporter, Framework

reporter = ComplianceReporter(policy=your_policy, audit_trail=your_audit)
reporter.generate_pdf(Framework.HIPAA, output_path="hipaa_report.pdf")
reporter.generate_pdf(Framework.EU_AI_ACT, output_path="eu_ai_act_report.pdf")
```

---

## What it does

AgentMesh sits between your engineers and every LLM API. It enforces token budgets, semantically caches repeated prompts, and routes calls to the cheapest capable model — without touching a single line of agent code.

```
Claude Code / VS Code Copilot / Cursor
ChatGPT web / Claude.ai / Gemini web          ──►  AgentMesh Proxy  ──►  Anthropic
Your LangGraph / CrewAI / AutoGen agents                                   OpenAI
                                                                           Google
```

**It catches everything** — not just the agents you wrote, but also the AI tools your engineers use every day in their browsers.

---

## Benchmark — real numbers, demo mode, no API keys needed

```bash
pip install agentmesh-proxy sentence-transformers
python examples/benchmark.py
```

20 requests across 5 topic clusters, each cluster with 4 phrasings (persona prefix, markdown, British spelling, plain):

```
Total requests          20
Exact cache hits         2  (10%)
Semantic cache hits     15  (75%)
Total misses             3  (15%)

Cost WITHOUT AgentMesh  $0.0030  ($3/M token baseline)
Cost WITH AgentMesh     $0.0008
Savings                 $0.0023  (75%)
Effective cost/request  $0.00004
```

**85% of requests never reached the LLM.** The 3 misses are the cold-start first call per cluster.

---

## The problem it solves

- **Uber** burned through their entire 2026 AI budget in 4 months
- **Amazon** shut down an internal AI leaderboard because engineers ran pointless loops to inflate scores ("tokenmaxxing")
- A single recursive agent loop, undetected, can generate a **$47,000 API bill**
- Only **38%** of enterprises have end-to-end AI cost monitoring (Cloud Security Alliance, 2026)

The root cause: every AI tool — Claude Code, GitHub Copilot, ChatGPT, your custom agents — talks to LLM APIs independently, with no shared governance layer. **AgentMesh is that layer.**

---

## Three ways to use it

### 1. Proxy mode — zero code changes, covers everything

```bash
pip install agentmesh-proxy
agentmesh serve --port 8080 --demo
```

Point any tool at `localhost:8080`:

```bash
# Claude Code
export ANTHROPIC_BASE_URL=http://localhost:8080

# VS Code Copilot / Cursor / any OpenAI SDK tool
export OPENAI_BASE_URL=http://localhost:8080/v1

# curl test
curl http://localhost:8080/v1/messages \
  -H "x-api-key: any" \
  -H "X-AgentMesh-Team: engineering" \
  -d '{"model":"claude-haiku-4-5","max_tokens":512,"messages":[{"role":"user","content":"Review this code..."}]}'
```

Every response includes governance headers:

```
X-AgentMesh-Cache:     hit          # exact | semantic | miss
X-AgentMesh-Tokens:    0            # 0 on cache hit
X-AgentMesh-Cost-USD:  0.000000     # $0 on cache hit
X-AgentMesh-Quota-Pct: 23%          # team budget consumed
X-AgentMesh-Vendor:    anthropic
X-AgentMesh-Model:     claude-haiku-4-5
```

### 2. Chrome Extension — governance for ChatGPT, Claude.ai, Gemini

The extension intercepts prompts typed into web AI tools before they hit the LLM. It shows a governance overlay on every submission, checks the semantic cache, and displays per-session savings in a popup.

**Load the extension:**

1. Clone this repo: `git clone https://github.com/anilatambharii/agentmesh`
2. Generate icons: `cd agentmesh-extension && python generate_icons.py`
3. Open `chrome://extensions` → Enable Developer Mode → Load Unpacked → select `agentmesh-extension/`
4. Click the AgentMesh popup → set Port to match your running proxy

**What it catches:**
- `chat.openai.com` / `chatgpt.com` — content script intercepts input box
- `claude.ai` — content script intercepts input box
- `gemini.google.com` — content script intercepts input box
- `api.anthropic.com` / `api.openai.com` — declarativeNetRequest silently redirects API calls

**How the overlay works:**

```
  ┌─────────────────────────────────────────┐
  │  AgentMesh Governance                   │
  │                                         │
  │  [●] Cache HIT — saved 847 tokens       │
  │  Team: engineering   Quota: 23%         │
  │                                         │
  │  [Send original]  [Cancel]              │
  └─────────────────────────────────────────┘
```

**Popup stats (persist across Chrome restarts):**

```
AgentMesh Connected
3  Prompts
2  Cache Hits
87 Tokens Saved
$0.002 Cost Saved
```

### 3. SDK mode — wrap existing agents

```python
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy

policy = Policy.from_yaml("""
policies:
  - name: engineering-team
    budget:
      daily_tokens: 1_000_000
      monthly_usd: 3_000
      hard_stop: true
    circuit_breaker:
      max_iterations: 25
    compliance:
      frameworks: [eu-ai-act, soc2]
""")

mesh = AgentMesh(policy=policy)

# Zero changes to agent code
governed_graph = mesh.wrap_langgraph(your_langgraph_graph)
governed_crew  = mesh.wrap_crewai(your_crew)
governed_agent = mesh.wrap_openai_agent(your_openai_agent)
```

---

## Three-layer cache

Every prompt passes through three cache layers before touching an LLM:

```
Layer 1 — Exact match      SHA-256 of normalised prompt    → 0 tokens, instant
Layer 2 — Semantic match   sentence-transformers cosine    → 0 tokens, ~5ms
Layer 3 — Vendor cache     Anthropic cache_control         → 10% of input cost
```

**Layer 2 catches prompts that mean the same thing but are worded differently:**

| Original | Rephrased | Similarity | Result |
|---|---|---|---|
| `Review this microservices design...` | `You are a senior architect. Review...` | 0.99 | HIT — persona stripped |
| `Review this microservices design...` | `Analyse this distributed system...` | 0.85 | HIT — British spelling normalised |
| `Review this microservices design...` | `**Review** this \`microservices\` design...` | 0.97 | HIT — markdown stripped |
| `Review this microservices design...` | `Review this distributed system: orders calls payments via REST...` | 0.70 | HIT — semantic match |
| `Review this microservices design...` | `Write a Fibonacci function in Python` | -0.05 | MISS — correctly different |

**Normalisation pipeline** (applied before hashing and embedding):

1. Persona prefix strip — `"You are a senior SWE."` removed
2. Filler word strip — `"Please can you"` removed
3. Markdown strip — `**bold**`, `# headers`, `` `code` `` removed
4. Date normalisation — `"June 13 2026"` → `"2026-06-13"`
5. Number normalisation — `"1,000,000"` → `"1000000"`
6. British→American spelling — `"optimise"` → `"optimize"`
7. Code argument canonicalisation — `login(user, pwd)` ≡ `login(username, password)`
8. Lowercase + whitespace collapse

---

## Token quota governance

Per-team, per-user, per-tool limits with pre-call blocking and real-time observability.

```bash
# Start proxy with team limits
agentmesh serve --port 8080 \
  --team-limit engineering=2000000 \
  --team-limit sales=500000 \
  --warn-at 80 \
  --hard-stop-at 100
```

```
# Request from a team at 85% quota
X-AgentMesh-Quota-Pct:  85%
X-AgentMesh-Quota-Warn: Quota warning: team 'engineering' at 85% (300,000 tokens remaining)

# Request from a team at 102% quota → 429
HTTP 429
{"error": {"type": "quota_exceeded", "message": "Quota exceeded: team 'engineering' used 2,040,000/2,000,000 tokens"}}
```

New in this release:
- **Pre-call blocking** — blocked before the LLM call using estimated token count, not after
- **Global vs team conflict resolution** — all quota dimensions checked; most restrictive wins
- **Temp grant expiry** — emergency escalation grants expire after 24h (configurable)

---

## Architecture

```
Engineers                    Business users
──────────────────────────   ──────────────────────────────────────
Claude Code (terminal)       ChatGPT web  ──► Chrome Extension
VS Code Copilot (IDE)        Claude.ai    ──► Chrome Extension
Cursor (IDE)                 Gemini web   ──► Chrome Extension
Your agents (LangGraph etc.) ──────────────────────────────────────
         │                              │
         │  ANTHROPIC_BASE_URL          │  declarativeNetRequest
         │  = http://localhost:8080     │  api.anthropic.com ──► localhost:8080
         │                              │  api.openai.com   ──► localhost:8080
         └──────────────┬───────────────┘
                        │
              ┌─────────▼──────────┐
              │   AgentMesh Proxy  │
              │                    │
              │  1. Circuit breaker│   kill runaway loops first
              │  2. Quota check    │   pre-call estimation
              │  3. Exact cache    │   SHA-256 → 0 tokens
              │  4. Semantic cache │   sentence-transformers cosine
              │  5. Vendor route   │   cheapest capable model
              │  6. Provider cache │   Anthropic cache_control
              │  7. LLM call       │   only if all caches missed
              │  8. Cache store    │   semantic + exact
              │  9. Audit log      │   Ed25519 tamper-evident
              └─────────┬──────────┘
                        │
          ┌─────────────┼──────────────┐
          ▼             ▼              ▼
     Anthropic       OpenAI         Google
     (Haiku/Sonnet)  (GPT-4o-mini)  (Gemini Flash)
```

---

## Observability dashboard

```bash
agentmesh observe --port 7861   # SSE event stream
```

Or start everything together:

```bash
agentmesh serve --port 8080 --demo --observe
# Opens: http://localhost:7860  (Gradio dashboard)
#        http://localhost:7861  (SSE stream)
#        http://localhost:8080  (proxy)
```

Events streamed in real time:

```json
{"kind": "cache_hit",   "team": "engineering", "tokens_saved": 847}
{"kind": "cache_miss",  "team": "engineering", "model": "claude-haiku-4-5"}
{"kind": "quota_warn",  "team": "engineering", "quota_pct": 0.85}
{"kind": "quota_block", "team": "sales",       "quota_pct": 1.02}
{"kind": "llm_call",    "vendor": "anthropic", "tokens": 1234, "cost_usd": 0.000185}
```

---

## Quickstart (60 seconds)

```bash
# 1. Install
pip install agentmesh-proxy sentence-transformers

# 2. Start proxy in demo mode (no API keys needed)
agentmesh serve --port 8080 --demo

# 3. Point Claude Code at it
export ANTHROPIC_BASE_URL=http://localhost:8080

# 4. Run the benchmark
python examples/benchmark.py
# → 85% cache hit rate, 75% cost reduction on 20 requests

# 5. Run the full test suite
python examples/test_extension_e2e.py
# → 13/13 PASS
```

---

## Framework support

| Framework | Status |
|---|---|
| LangGraph | Full support |
| CrewAI | Full support |
| OpenAI Agents SDK | Full support |
| AutoGen v2 / AG2 | Full support |
| Pydantic AI | Full support |
| Haystack 2.x | Full support |
| Google ADK | Full support |
| NVIDIA NIM | Full support |
| Raw `anthropic` / `openai` SDK | Full support |
| Chrome extension (ChatGPT, Claude.ai, Gemini) | Full support |
| Microsoft Semantic Kernel | In progress (v0.3) |

---

## What's new (June 2026)

- **Chrome Extension** — governance overlay for ChatGPT, Claude.ai, Gemini web
- **sentence-transformers semantic cache** — 384-dim embeddings replace character bigrams; catches paraphrased prompts at 0.70 cosine threshold
- **Anthropic prompt caching** — `cache_control: ephemeral` wired into every system prompt (10% of normal input cost on cached reads)
- **Streaming cache** — streamed responses now accumulated and cached after completion
- **Pre-call quota blocking** — blocked before the LLM call using token estimation
- **Normalisation pipeline** — markdown, dates, British spelling, persona prefixes all stripped before cache key generation
- **Stats persistence** — Chrome extension stats survive service worker restarts

---

## Roadmap

- [ ] Redis cache backend (shared across proxy instances)
- [ ] VS Code extension (native IDE panel)
- [ ] SAML/SSO identity propagation for enterprise quota
- [ ] Slack/Teams bot intercept
- [ ] OpenTelemetry trace export
- [ ] Per-prompt cost alerts (Slack/PagerDuty webhook)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome — especially Redis backend, VS Code extension, and additional vendor support.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

*Built by [Anil Prasad](https://github.com/anilatambharii) — open to feedback, collabs, and conversations about enterprise AI governance.*
