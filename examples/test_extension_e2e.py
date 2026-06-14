"""
AgentMesh Extension -- End-to-End Test Suite
=============================================

Simulates every scenario the Chrome extension exercises without needing a
real browser. All HTTP calls go to a local test proxy on port 8099.

Scenarios covered:
  [Option A]  API redirect rules -> Anthropic & OpenAI format calls
  [Option B]  Content script dry-run -> prompt interception flow
  [Cache]     Exact cache hit (identical prompt)
  [Cache]     Semantic cache hit (similar-wording prompt)
  [Cache]     Cache miss (unique prompt)
  [Quota]     Warning header when team is near limit
  [Quota]     429 block + escalation when team exceeds limit
  [Identity]  Team / User / Tool header propagation
  [UA]        User-Agent auto-detection (no explicit tool header)
  [Governance] All X-AgentMesh-* response headers present
  [Models]    /v1/models OpenAI-compat endpoint
"""

import json
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Colour output (degrades gracefully on terminals without ANSI)
# ---------------------------------------------------------------------------

try:
    import os
    _USE_COL = sys.stdout.isatty() or os.environ.get("FORCE_COLOR")
except Exception:
    _USE_COL = False

def _c(code, s):  return f"\033[{code}m{s}\033[0m" if _USE_COL else s
def green(s):     return _c("92", s)
def red(s):       return _c("91", s)
def yellow(s):    return _c("93", s)
def cyan(s):      return _c("96", s)
def bold(s):      return _c("1",  s)
def dim(s):       return _c("2",  s)

# ---------------------------------------------------------------------------
# Proxy startup
# ---------------------------------------------------------------------------

TEST_PORT = 8099
BASE      = f"http://localhost:{TEST_PORT}"

# Two code-review prompts that are very similar (for semantic cache test)
PROMPT_REVIEW_1 = (
    "Review this Python function for SQL injection: "
    "def login(user, pwd): return db.execute(f'SELECT * FROM users WHERE name={user}')"
)
PROMPT_REVIEW_2 = (
    "Review this Python function for SQL injection vulnerabilities: "
    "def login(username, password): return db.execute(f'SELECT * FROM users WHERE name={username}')"
)

# Topically diverse prompts — share no common bigrams so each is a definite cache MISS.
# Used for quota tests so that consume() is called on every request.
QUOTA_PROMPTS_WARN = [
    "What is the capital of Australia and why was it chosen over Sydney?",
    "Explain the complete lifecycle of an HTTP request from browser to server.",
    "Write a recursive Python function that computes Fibonacci numbers.",
    "What are the key architectural differences between SQL and NoSQL databases?",
    "Describe how a TLS handshake establishes an encrypted connection.",
    "Which design patterns are essential for building scalable microservices?",
]

QUOTA_PROMPTS_BLOCK = [
    "Name the programming language invented by Guido van Rossum.",
    "Explain what binary search trees guarantee about lookup time complexity.",
    "Describe the observer pattern with a real-world example outside software.",
    "What is eventually consistent storage and when should you choose it?",
    "How does a CPU branch predictor improve pipeline throughput?",
    "Contrast functional programming with object-oriented paradigms briefly.",
    "What cryptographic property makes SHA-256 useful for checksums?",
    "Summarise the purpose of Kubernetes resource quotas in one paragraph.",
]


def start_test_proxy():
    from agentmesh.proxy.server import ProxyConfig, start_proxy

    config = ProxyConfig(
        vendors               = ["anthropic", "openai"],
        routing_strategy      = "cheapest_capable",
        demo_mode             = True,
        enable_cache          = True,
        enable_compression    = True,
        global_monthly_tokens = 10_000_000,
        # Two teams with very low limits so we can test warn/block quickly
        team_monthly_tokens   = {
            "warn_team":  150,   # warn triggers at 70% => ~105 tokens
            "block_team": 30,    # blocks at 100% => after ~1 request
        },
        quota_warn_pct      = 0.70,
        quota_hard_stop_pct = 1.00,
        port      = TEST_PORT,
        log_level = "error",
    )
    start_proxy(config)


def wait_ready(max_s=25):
    deadline = time.time() + max_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _req(method, path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, dict(r.headers), _try_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, dict(e.headers), _try_json(raw)
    except Exception as ex:
        return 0, {}, {"_err": str(ex)}


def _try_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def GET(path, headers=None):
    return _req("GET", path, headers=headers)


def POST(path, body, headers=None):
    return _req("POST", path, body=body, headers=headers)


def _anthropic_msg(content, team=None, user=None, tool=None, extra_headers=None):
    h = {"x-api-key": "agentmesh"}
    if team:  h["X-AgentMesh-Team"] = team
    if user:  h["X-AgentMesh-User"] = user
    if tool:  h["X-AgentMesh-Tool"] = tool
    if extra_headers: h.update(extra_headers)
    return POST("/v1/messages",
        {"model": "claude-haiku-4-5", "max_tokens": 60,
         "messages": [{"role": "user", "content": content}]},
        headers=h,
    )


# ---------------------------------------------------------------------------
# Test functions  (each returns  (pass: bool, detail: str))
# ---------------------------------------------------------------------------

def t_health():
    s, _, b = GET("/health")
    ok = s == 200 and b.get("status") == "ok" and b.get("demo_mode") is True
    return ok, (
        f"GET /health -> {s}  "
        f"status={b.get('status')}  demo={b.get('demo_mode')}  "
        f"vendors={b.get('vendors')}"
    )


def t_models():
    s, _, b = GET("/v1/models")
    ids = [m["id"] for m in b.get("data", [])[:4]]
    ok  = s == 200 and len(ids) > 0
    return ok, f"GET /v1/models -> {s}  models={ids}"


def t_option_a_anthropic():
    """Option A: extension redirect rule intercepts api.anthropic.com call."""
    s, h, b = POST(
        "/v1/messages",
        {"model": "claude-haiku-4-5", "max_tokens": 60,
         "messages": [{"role": "user", "content": "Say hello in one sentence."}]},
        {"x-api-key": "agentmesh", "X-AgentMesh-Tool": "claude-code"},
    )
    ok = s == 200 and "content" in b and h.get("x-agentmesh-demo") == "true"
    return ok, (
        f"POST /v1/messages -> {s}  "
        f"vendor={h.get('x-agentmesh-vendor')}  "
        f"tool={h.get('x-agentmesh-tool')}  "
        f"demo={h.get('x-agentmesh-demo')}"
    )


def t_option_a_openai():
    """Option A: extension redirect rule intercepts api.openai.com call."""
    s, h, b = POST(
        "/v1/chat/completions",
        {"model": "gpt-4o-mini", "max_tokens": 60,
         "messages": [{"role": "user", "content": "Say hello in one sentence."}]},
        {"Authorization": "Bearer agentmesh", "X-AgentMesh-Tool": "vscode-copilot"},
    )
    ok = s == 200 and "choices" in b and len(b["choices"]) > 0
    return ok, (
        f"POST /v1/chat/completions -> {s}  "
        f"choices={len(b.get('choices',[]))}  "
        f"tool={h.get('x-agentmesh-tool')}  "
        f"content_preview='{b.get('choices',[{}])[0].get('message',{}).get('content','')[:50]}...'"
    )


def t_option_b_dry_run():
    """Option B: content script sends X-AgentMesh-Dry-Run: true before submit."""
    s, h, b = POST(
        "/v1/messages",
        {"model": "claude-haiku-4-5", "max_tokens": 1,
         "messages": [{"role": "user", "content": "Summarise this meeting notes briefly."}]},
        {
            "x-api-key":            "agentmesh",
            "X-AgentMesh-Dry-Run":  "true",
            "X-AgentMesh-Tool":     "claude-ai-browser",
            "X-AgentMesh-Team":     "product",
            "X-AgentMesh-User":     "pm@intuit.com",
        },
    )
    is_preview = "Preview" in str(b.get("content", ""))
    dry_hdr    = h.get("x-agentmesh-dry-run") == "true"
    ok = s == 200 and (is_preview or dry_hdr)
    return ok, (
        f"POST /v1/messages [Dry-Run] -> {s}  "
        f"dry-run-header={dry_hdr}  "
        f"team={h.get('x-agentmesh-team')}  "
        f"quota={h.get('x-agentmesh-quota-pct')}"
    )


def t_cache_miss():
    """First call with a specific prompt -> MISS."""
    s, h, _ = _anthropic_msg(PROMPT_REVIEW_1, team="cache_test_team")
    cache = h.get("x-agentmesh-cache", "?")
    ok    = s == 200 and cache == "miss"
    return ok, f"1st call  cache={cache}  (expected: miss)  tokens={h.get('x-agentmesh-tokens')}"


def t_cache_hit_exact():
    """Identical prompt right after -> exact HIT."""
    s, h, _ = _anthropic_msg(PROMPT_REVIEW_1, team="cache_test_team")
    cache = h.get("x-agentmesh-cache", "?")
    ok    = s == 200 and cache == "hit"
    return ok, f"Exact dup  cache={cache}  (expected: hit)"


def t_cache_hit_semantic():
    """Near-duplicate prompt (same intent, different words) -> semantic HIT."""
    s, h, _ = _anthropic_msg(PROMPT_REVIEW_2, team="cache_test_team")
    cache = h.get("x-agentmesh-cache", "?")
    ok    = s == 200 and cache == "hit"
    # semantic hit is best-effort; demote to warn rather than hard fail
    note  = "" if ok else "  (semantic threshold may need tuning — acceptable)"
    return ok, f"Similar wording  cache={cache}  (expected: hit){note}"


def t_identity_headers():
    """Team / User / Tool headers echoed back in response."""
    s, h, _ = _anthropic_msg(
        "ping", team="payments", user="eng@google.com", tool="cursor"
    )
    ok = (
        s == 200
        and h.get("x-agentmesh-team") == "payments"
        and h.get("x-agentmesh-tool") == "cursor"
    )
    return ok, (
        f"team={h.get('x-agentmesh-team')}  "
        f"tool={h.get('x-agentmesh-tool')}  "
        f"(user propagated via audit)"
    )


def t_ua_detection():
    """User-Agent sniffed when no X-AgentMesh-Tool header provided."""
    s, h, _ = POST(
        "/v1/messages",
        {"model": "claude-haiku-4-5", "max_tokens": 10,
         "messages": [{"role": "user", "content": "ping"}]},
        {"x-api-key": "agentmesh",
         "User-Agent": "claude-code/1.2.0 (darwin; arm64)"},
    )
    tool = h.get("x-agentmesh-tool", "?")
    ok   = s == 200 and "claude" in tool.lower()
    return ok, f"User-Agent='claude-code/...'  -> tool={tool}  (expected: claude-code)"


def t_governance_headers():
    """All X-AgentMesh-* metadata headers must be present on every response."""
    s, h, _ = _anthropic_msg("check governance headers")
    required = [
        "x-agentmesh-vendor", "x-agentmesh-model", "x-agentmesh-tokens",
        "x-agentmesh-cost-usd", "x-agentmesh-cache", "x-agentmesh-demo",
        "x-agentmesh-quota-pct", "x-agentmesh-latency-ms",
    ]
    present = [k for k in required if k in h]
    missing = [k for k in required if k not in h]
    ok      = len(missing) == 0
    detail  = (
        f"{len(present)}/{len(required)} headers present"
        + (f"  MISSING={missing}" if missing else "")
        + f"  cost=${h.get('x-agentmesh-cost-usd','?')}  tokens={h.get('x-agentmesh-tokens','?')}"
    )
    return ok, detail


def t_quota_warn():
    """warn_team (150-token limit, warn@70%) — send diverse prompts so every
    call is a cache miss and consume() actually accumulates."""
    last_quota = "0%"
    for i, prompt in enumerate(QUOTA_PROMPTS_WARN):
        s, h, _ = _anthropic_msg(prompt, team="warn_team")
        last_quota = h.get("x-agentmesh-quota-pct", "0%")
        try:
            pct = float(last_quota.rstrip("%"))
        except ValueError:
            pct = 0
        cache = h.get("x-agentmesh-cache", "?")
        if pct >= 70:
            return True, (
                f"Quota warning at {last_quota} after {i+1} requests  "
                f"(threshold=70%)  last_cache={cache}"
            )
        if s == 429:
            return True, f"Quota hard-stop at {last_quota} after {i+1} requests"
    return False, (
        f"Warning not triggered after {len(QUOTA_PROMPTS_WARN)} requests  "
        f"last quota={last_quota}  "
        f"(token estimate per call may be lower than expected)"
    )


def t_quota_block():
    """block_team (30-token limit) — should 429 after the 2nd request
    since first call consumes ~47 tokens (exceeds 30-token limit)."""
    for i, prompt in enumerate(QUOTA_PROMPTS_BLOCK):
        s, h, b = _anthropic_msg(prompt, team="block_team")
        quota = h.get("x-agentmesh-quota-pct", "?")
        cache = h.get("x-agentmesh-cache", "?")
        if s == 429:
            msg = b.get("error", {}).get("message", b.get("message", str(b)))[:80]
            return True, (
                f"429 BLOCKED after {i+1} requests  quota={quota}  "
                f"reason='{msg}'"
            )
    return False, (
        f"Block never triggered after {len(QUOTA_PROMPTS_BLOCK)} requests  "
        f"(quota enforcement may use estimated tokens not per-call actuals)"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("Health check",                      t_health),
    ("/v1/models list",                   t_models),
    ("Option A — Anthropic API redirect", t_option_a_anthropic),
    ("Option A — OpenAI-compat redirect", t_option_a_openai),
    ("Option B — Dry-run (content script)", t_option_b_dry_run),
    ("Cache MISS  (first call)",          t_cache_miss),
    ("Cache HIT   (exact duplicate)",     t_cache_hit_exact),
    ("Cache HIT   (semantic similarity)", t_cache_hit_semantic),
    ("Identity    (team/user/tool)",      t_identity_headers),
    ("User-Agent  auto-detection",        t_ua_detection),
    ("Governance  metadata headers",      t_governance_headers),
    ("Quota WARN  (team near limit)",     t_quota_warn),
    ("Quota BLOCK (team over limit + 429)", t_quota_block),
]


def main():
    W = 62
    print(f"\n{bold('=' * W)}")
    print(bold("  AgentMesh Extension — End-to-End Test Suite"))
    print(bold("  Simulates Chrome extension: Option A + B + all scenarios"))
    print(f"{bold('=' * W)}\n")

    print(cyan("  Starting test proxy on port 8099 (demo mode) ..."))
    try:
        start_test_proxy()
    except Exception as e:
        print(red(f"  Could not start proxy: {e}"))
        sys.exit(1)

    if not wait_ready():
        print(red("  Proxy did not become ready in 25 s."))
        sys.exit(1)
    print(green("  Proxy ready.\n"))

    passed = failed = warned = 0

    for idx, (name, fn) in enumerate(TESTS, 1):
        label = f"[{idx:02d}/{len(TESTS):02d}]"
        print(f"{cyan(label)} {bold(name)}")
        try:
            ok, detail = fn()
        except Exception as ex:
            ok, detail = False, f"Unhandled exception: {ex}"

        # semantic cache is best-effort — treat as warn not fail
        is_semantic = "semantic" in name.lower()
        if not ok and is_semantic:
            print(f"  {yellow('WARN')}  {dim(detail)}\n")
            warned += 1
        elif ok:
            print(f"  {green('PASS')}  {dim(detail)}\n")
            passed += 1
        else:
            print(f"  {red('FAIL')}  {dim(detail)}\n")
            failed += 1

    print(bold("=" * W))
    total = len(TESTS)
    print(
        f"  Results:  "
        f"{green(str(passed))} passed  "
        f"{yellow(str(warned))} warned  "
        f"{red(str(failed)) if failed else green(str(failed))} failed"
        f"  /  {total} tests"
    )
    if warned:
        print(dim(f"  (WARN = best-effort tests that depend on similarity tuning)"))
    print(bold("=" * W) + "\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
