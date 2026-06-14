"""
Vendor HTTP forwarder — sends governed requests to the actual LLM APIs.

Supports:
  - Anthropic   (https://api.anthropic.com)
  - OpenAI      (https://api.openai.com)
  - Google      (OpenAI-compatible Gemini endpoint)
  - Azure       (OpenAI-compatible)
  - Mistral     (OpenAI-compatible)
  - Cohere      (OpenAI-compatible)

Demo / test mode: returns a realistic mock response when no API key is configured.

Usage:
    result = await forward(vendor="anthropic", model="claude-haiku-4-5",
                           messages=[...], max_tokens=512, api_key="sk-...")
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ── Vendor endpoint config ────────────────────────────────────────────────────

VENDOR_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "anthropic": {
        "url":     "https://api.anthropic.com/v1/messages",
        "fmt":     "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "url":     "https://api.openai.com/v1/chat/completions",
        "fmt":     "openai",
        "env_key": "OPENAI_API_KEY",
    },
    "google": {
        "url":     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "fmt":     "openai",
        "env_key": "GOOGLE_API_KEY",
    },
    "azure_openai": {
        "url":     os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        "fmt":     "openai",
        "env_key": "AZURE_OPENAI_KEY",
    },
    "mistral": {
        "url":     "https://api.mistral.ai/v1/chat/completions",
        "fmt":     "openai",
        "env_key": "MISTRAL_API_KEY",
    },
    "cohere": {
        "url":     "https://api.cohere.com/compatibility/v1/chat/completions",
        "fmt":     "openai",
        "env_key": "COHERE_API_KEY",
    },
}

MOCK_RESPONSES = [
    "Based on your request, I've analyzed the code and found potential improvements in the authentication flow. The JWT validation on line 47 should use a constant-time comparison to prevent timing attacks.",
    "The architecture looks solid overall. I recommend extracting the data transformation logic into a separate service layer — this would improve testability and make the code easier to maintain as the system scales.",
    "I've reviewed the PR diff. The main concern is the N+1 query pattern in UserRepository.get_orders(). Switching to a JOIN would reduce database round-trips from O(n) to O(1), likely an 85% latency improvement under load.",
    "The security scan is complete. No SQL injection vulnerabilities detected. One concern: user input is passed to os.path.join() on line 147 — recommend using pathlib.Path instead to prevent path traversal.",
    "Performance analysis complete. The caching layer is correctly implemented. Memory usage could be further optimized by switching from dict to an LRU cache with a bounded size (recommend 10,000 entries max).",
]


def get_api_key(vendor: str, client_key: Optional[str] = None) -> Optional[str]:
    """
    Resolve the API key to use.
    Priority: client-provided key > environment variable.
    """
    if client_key and client_key not in ("agentmesh", "any-value-here", "sk-agentmesh"):
        return client_key
    cfg = VENDOR_ENDPOINTS.get(vendor, {})
    return os.environ.get(cfg.get("env_key", ""), None)


def has_api_key(vendor: str, client_key: Optional[str] = None) -> bool:
    return bool(get_api_key(vendor, client_key))


def mock_llm_response(
    messages:      List[Dict[str, Any]],
    model:         str,
    vendor:        str,
    max_tokens:    int = 1024,
) -> Dict[str, Any]:
    """
    Return a realistic mock LLM response — used in demo mode or when no API key is set.
    Simulates realistic token counts and latency.
    """
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    # Pick a response deterministically based on the input
    idx = hash(str(last_user)) % len(MOCK_RESPONSES)
    content = MOCK_RESPONSES[idx]

    input_tokens  = max(10, len(str(last_user)) // 4)
    output_tokens = max(10, len(content) // 4)

    # Return in Anthropic format (forwarder translates as needed)
    return {
        "id":           f"msg_{uuid.uuid4().hex[:16]}",
        "type":         "message",
        "role":         "assistant",
        "content":      [{"type": "text", "text": f"[AgentMesh Demo — {vendor}/{model}] {content}"}],
        "model":        model,
        "stop_reason":  "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
        },
        "_demo": True,
    }


async def forward(
    vendor:     str,
    model:      str,
    messages:   List[Dict[str, Any]],
    max_tokens: int            = 1024,
    temperature: float         = 1.0,
    stream:     bool           = False,
    client_key: Optional[str]  = None,
    timeout:    float          = 120.0,
    demo_mode:  bool           = False,
) -> Dict[str, Any]:
    """
    Forward a governed LLM request to the actual vendor API.
    Returns the raw vendor response as a dict (Anthropic format).

    Falls back to demo mode if:
      - demo_mode=True
      - No API key available for the vendor
      - httpx not installed
    """
    api_key = get_api_key(vendor, client_key)

    if demo_mode or not api_key or not HAS_HTTPX:
        return mock_llm_response(messages, model, vendor, max_tokens)

    cfg = VENDOR_ENDPOINTS.get(vendor)
    if not cfg:
        return mock_llm_response(messages, model, vendor, max_tokens)

    url = cfg["url"]
    fmt = cfg["fmt"]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if fmt == "anthropic":
                return await _call_anthropic(client, url, api_key, model, messages, max_tokens, temperature)
            else:
                return await _call_openai_compat(client, url, api_key, model, messages, max_tokens, temperature)
    except Exception as exc:
        # Never surface API errors to the governance layer — return a safe fallback
        return {
            **mock_llm_response(messages, model, vendor, max_tokens),
            "_error": str(exc),
        }


async def forward_stream(
    vendor:     str,
    model:      str,
    messages:   List[Dict[str, Any]],
    max_tokens: int           = 1024,
    temperature: float        = 1.0,
    client_key: Optional[str] = None,
    demo_mode:  bool          = False,
) -> AsyncGenerator[bytes, None]:
    """
    Stream a response from the vendor as raw SSE bytes.
    Falls back to a single mock chunk in demo mode.
    """
    if demo_mode or not has_api_key(vendor, client_key) or not HAS_HTTPX:
        mock = mock_llm_response(messages, model, vendor, max_tokens)
        content = mock["content"][0]["text"]
        # Emit Anthropic-style SSE chunks
        yield b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        # Chunk the text into ~20 char pieces
        for i in range(0, len(content), 20):
            chunk = content[i:i+20]
            payload = json.dumps({"type": "content_block_delta", "index": 0,
                                  "delta": {"type": "text_delta", "text": chunk}})
            yield f"data: {payload}\n\n".encode()
        yield b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":30}}\n\n'
        yield b'data: {"type":"message_stop"}\n\n'
        return

    api_key = get_api_key(vendor, client_key)
    cfg = VENDOR_ENDPOINTS.get(vendor, {})
    url = cfg.get("url", "")
    fmt = cfg.get("fmt", "anthropic")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if fmt == "anthropic":
                headers = {
                    "x-api-key":          api_key,
                    "anthropic-version":  "2023-06-01",
                    "content-type":       "application/json",
                    "anthropic-beta":     "prompt-caching-2024-07-31",
                }
                sys_msgs = [m for m in messages if m["role"] == "system"]
                usr_msgs = [m for m in messages if m["role"] != "system"]
                payload  = {
                    "model":       model,
                    "max_tokens":  max_tokens,
                    "messages":    usr_msgs,
                    "stream":      True,
                    "temperature": temperature,
                }
                if sys_msgs:
                    payload["system"] = [{
                        "type": "text",
                        "text": sys_msgs[0]["content"],
                        "cache_control": {"type": "ephemeral"},
                    }]
            else:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "content-type":  "application/json",
                }
                payload = {
                    "model":       model,
                    "messages":    messages,
                    "max_tokens":  max_tokens,
                    "temperature": temperature,
                    "stream":      True,
                }

            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
    except Exception as exc:
        err = json.dumps({"type": "error", "error": {"message": str(exc)}})
        yield f"data: {err}\n\n".encode()


# ── Private helpers ───────────────────────────────────────────────────────────

async def _call_anthropic(
    client, url, api_key, model, messages, max_tokens, temperature
) -> Dict[str, Any]:
    sys_msgs = [m for m in messages if m["role"] == "system"]
    usr_msgs = [m for m in messages if m["role"] != "system"]
    payload  = {
        "model":       model,
        "max_tokens":  max_tokens,
        "messages":    usr_msgs,
        "temperature": temperature,
    }
    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
        # Enable server-side prompt caching — system prompt reads cost 10% after first call.
        "anthropic-beta":    "prompt-caching-2024-07-31",
    }
    if sys_msgs:
        # cache_control: ephemeral pins this system prompt in Anthropic's cache for ~5 min.
        payload["system"] = [{
            "type": "text",
            "text": sys_msgs[0]["content"],
            "cache_control": {"type": "ephemeral"},
        }]
    r = await client.post(url, json=payload, headers=headers)
    r.raise_for_status()
    return r.json()


async def _call_openai_compat(
    client, url, api_key, model, messages, max_tokens, temperature
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type":  "application/json",
    }
    payload = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }
    r = await client.post(url, json=payload, headers=headers)
    r.raise_for_status()
    data = r.json()

    # Normalize OpenAI response to Anthropic shape so forwarder is consistent
    choices = data.get("choices", [])
    content = choices[0]["message"]["content"] if choices else ""
    usage   = data.get("usage", {})
    return {
        "id":    data.get("id", f"msg_{uuid.uuid4().hex[:8]}"),
        "type":  "message",
        "role":  "assistant",
        "content": [{"type": "text", "text": content}],
        "model": data.get("model", model),
        "stop_reason": choices[0].get("finish_reason", "stop") if choices else "stop",
        "usage": {
            "input_tokens":  usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }
