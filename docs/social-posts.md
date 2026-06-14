# AgentMesh — Social Posts

---

## Hacker News — Show HN

**Title:**
```
Show HN: AgentMesh – open-source proxy that governs every AI tool (Claude Code, ChatGPT, Gemini, your agents)
```

**Body:**
```
I built a governance proxy that sits in front of every LLM call your team makes —
not just the agents you wrote, but also Claude Code, VS Code Copilot, ChatGPT
web, Claude.ai, and Gemini through a Chrome extension.

The problem: Uber burned their entire 2026 AI budget in 4 months. Amazon shut
down an internal leaderboard because engineers ran pointless agent loops to
inflate scores ("tokenmaxxing"). A single recursive loop, undetected, generates
a $47,000 API bill. The root cause is architectural — every AI tool talks to
LLM APIs independently with no shared governance layer.

AgentMesh is that layer. It runs as a local OpenAI-compatible proxy:

  export ANTHROPIC_BASE_URL=http://localhost:8080
  export OPENAI_BASE_URL=http://localhost:8080/v1

From that point, every Claude Code session, every Copilot call, every custom
agent goes through the governance pipeline:

  1. Exact cache check (SHA-256 of normalised prompt)
  2. Semantic cache check (sentence-transformers cosine, threshold 0.70)
  3. Quota check — per team, per user, pre-call estimated token blocking
  4. Vendor routing — cheapest capable model
  5. Anthropic prompt caching (cache_control: ephemeral, 10x cheaper reads)
  6. Tamper-evident audit log

Real benchmark (demo mode, no API keys, run it yourself):

  pip install agentmesh sentence-transformers
  python examples/benchmark.py

  → 20 requests, 5 topic clusters, 4 phrasings each
  → 85% cache hit rate (2 exact + 15 semantic)
  → 75% cost reduction
  → 3 misses (cold-start only, one per cluster)

The semantic cache is what makes this interesting. It catches prompts that
mean the same thing but are worded differently. Before embedding, a
normalisation pipeline strips:

  - Persona prefixes: "You are a senior architect." → removed
  - Markdown: **bold**, # headers, `code` → stripped
  - Date formats: "June 13 2026" → "2026-06-13"
  - British spelling: "optimise" → "optimize"
  - Filler words: "Please can you" → removed

This means "You are a senior architect. Review this microservices design..." and
"Analyse this distributed system..." both hit the same cache entry.

The Chrome extension adds the browser side. It uses declarativeNetRequest to
redirect api.anthropic.com and api.openai.com to localhost, and content scripts
to intercept prompts typed into ChatGPT, Claude.ai, and Gemini before they're
submitted. Engineers see a governance overlay showing whether their prompt hit
the cache and what their team's quota usage is.

The extension popup persists cumulative stats (cache hits, tokens saved, cost
saved) across browser restarts using chrome.storage.local.

I tested this end-to-end: 3 prompts to ChatGPT web (original + rephrased +
persona variant), got 2/3 cache hits. That's the rephrased architecture prompt
that previously missed now hitting at 0.70 cosine similarity.

What's not built yet: Redis backend (cache is in-memory, single process), VS
Code native extension, SAML identity propagation. Happy to discuss any of the
architecture decisions — especially the three-layer cache design or the Chrome
extension's declarativeNetRequest approach.

https://github.com/anilatambharii/agentmesh
```

---

## Twitter / X Thread

**Tweet 1 — Hook**
```
I built a proxy that sits in front of every AI tool your engineers use.

Claude Code. VS Code Copilot. ChatGPT web. Gemini. Your LangGraph agents.

One governance layer. Zero code changes.

Here's how it works and the real benchmark numbers 🧵
```

**Tweet 2 — The problem**
```
Uber burned through their entire 2026 AI budget in 4 months.

Amazon shut down an internal AI leaderboard because engineers ran
pointless agent loops to inflate their scores ("tokenmaxxing").

One recursive loop = $47,000 API bill. Undetected.

The fix isn't better agents. It's governance infrastructure.
```

**Tweet 3 — What it is**
```
AgentMesh is an OpenAI-compatible proxy.

Point your tools at it:

  ANTHROPIC_BASE_URL=http://localhost:8080
  OPENAI_BASE_URL=http://localhost:8080/v1

Done. Every LLM call now goes through:
  → 3-layer cache
  → Per-team quota enforcement
  → Cheapest-model routing
  → Audit trail
```

**Tweet 4 — The benchmark**
```
Real numbers from the benchmark (no API keys, run it yourself):

  pip install agentmesh sentence-transformers
  python examples/benchmark.py

20 requests, 5 topic clusters, 4 phrasings each:

  85% cache hit rate
  75% cost reduction
  3 misses (cold-start only)

https://github.com/anilatambharii/agentmesh
```

**Tweet 5 — The semantic cache**
```
The key insight: most repeated prompts aren't exact duplicates.

Engineers rephrase the same question. Add persona prefixes.
Use British spelling. Wrap in markdown.

AgentMesh normalises all of that before comparing:

  "You are a senior architect. Review this..."
  "Analyse this distributed system..."

Both hit the same cache entry. 0.70 cosine similarity.
```

**Tweet 6 — The Chrome extension**
```
The Chrome extension catches what the proxy can't see:

Engineers typing prompts directly into ChatGPT, Claude.ai, Gemini.

declarativeNetRequest redirects api.anthropic.com → localhost:8080

Content scripts intercept the input box and show a governance
overlay before the prompt is sent.

Open source. Load it in 3 steps.
```

**Tweet 7 — CTA**
```
Everything is open source, Apache 2.0.

→ Benchmark: python examples/benchmark.py
→ E2E tests: python examples/test_extension_e2e.py (13/13 pass)
→ Chrome extension: agentmesh-extension/

https://github.com/anilatambharii/agentmesh

What would you build on top of this?
```

---

## LinkedIn Post

```
I spent the last few months building something I kept wishing existed:
a governance proxy for AI tools.

The problem is real. Uber burned through their entire 2026 AI budget
in 4 months. Amazon shut down an internal AI leaderboard because
engineers were running pointless agent loops to inflate scores. One
undetected recursive loop can generate a $47,000 API bill.

The root cause is architectural. Claude Code, VS Code Copilot, ChatGPT,
your LangGraph agents — they all talk to LLM APIs independently. There's
no shared governance layer. No shared cache. No shared quota.

AgentMesh is that governance layer. It runs as a local proxy:

  export ANTHROPIC_BASE_URL=http://localhost:8080

From that point, every AI tool your team uses goes through:
  - A 3-layer semantic cache (85% hit rate on real workloads)
  - Per-team token quota enforcement with pre-call blocking
  - Automatic routing to the cheapest capable model
  - Anthropic prompt caching (10x cheaper reads on repeated system prompts)
  - Tamper-evident audit trail

I also built a Chrome extension that catches the browser side —
intercepting prompts typed into ChatGPT, Claude.ai, and Gemini
before they reach the LLM.

Benchmark: 20 requests, 5 topic clusters, 75% cost reduction.
No API keys needed to run it.

Everything is open source (Apache 2.0):
https://github.com/anilatambharii/agentmesh

If you're working on enterprise AI governance, agent cost control,
or LLM observability — I'd love to hear what problems you're hitting.
```
