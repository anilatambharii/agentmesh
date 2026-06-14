"""
Request / response format normalization.

AgentMesh proxy accepts two wire formats:
  - Anthropic  (/v1/messages)        — used by Claude Code, Anthropic SDK, Claude Desktop
  - OpenAI     (/v1/chat/completions) — used by VS Code Copilot, Cursor, most tools

Internally everything is normalized to OpenAI format (the widest lingua franca).
Responses are converted back to whatever format the client sent.
"""

from __future__ import annotations

import uuid
import time
from typing import Any, Dict, List, Optional, Tuple


# ── Inbound normalization ──────────────────────────────────────────────────────

def normalize_anthropic_request(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Anthropic /v1/messages format → internal OpenAI-style dict.

    Anthropic body:
        {model, system, messages, max_tokens, temperature, stream, ...}
    Internal:
        {model, messages: [{role:system,...}, ...], max_tokens, temperature, stream, _fmt:anthropic}
    """
    messages: List[Dict[str, Any]] = []

    # System prompt is a top-level field in Anthropic format
    system = body.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        # system can be a list of content blocks
        text = " ".join(
            block.get("text", "") for block in system if block.get("type") == "text"
        )
        if text:
            messages.append({"role": "system", "content": text})

    # User/assistant turns
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Content blocks: [{type:text, text:...}, ...]
            text = " ".join(
                block.get("text", "") for block in content if block.get("type") == "text"
            )
            messages.append({"role": role, "content": text})
        else:
            messages.append({"role": role, "content": str(content)})

    return {
        "model":       body.get("model", "claude-haiku-4-5"),
        "messages":    messages,
        "max_tokens":  body.get("max_tokens", 1024),
        "temperature": body.get("temperature", 1.0),
        "stream":      body.get("stream", False),
        "_fmt":        "anthropic",
    }


def normalize_openai_request(body: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI /v1/chat/completions → internal (mostly a passthrough)."""
    return {
        "model":       body.get("model", "gpt-4o-mini"),
        "messages":    body.get("messages", []),
        "max_tokens":  body.get("max_tokens", 1024),
        "temperature": body.get("temperature", 1.0),
        "stream":      body.get("stream", False),
        "_fmt":        "openai",
    }


def last_user_message(internal: Dict[str, Any]) -> str:
    """Extract the last user message text — used as semantic cache key."""
    for msg in reversed(internal.get("messages", [])):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def system_prompt(internal: Dict[str, Any]) -> str:
    """Extract the system prompt text."""
    for msg in internal.get("messages", []):
        if msg.get("role") == "system":
            return str(msg.get("content", ""))
    return ""


def estimate_input_tokens(internal: Dict[str, Any]) -> int:
    """Rough token estimate: ~4 chars per token."""
    total_chars = sum(len(str(m.get("content", ""))) for m in internal.get("messages", []))
    return max(1, total_chars // 4)


# ── Outbound formatting ───────────────────────────────────────────────────────

def to_anthropic_response(
    content: str,
    model:   str,
    input_tokens:  int = 0,
    output_tokens: int = 0,
    stop_reason:   str = "end_turn",
    cached:        bool = False,
) -> Dict[str, Any]:
    """Build an Anthropic-format /v1/messages response."""
    return {
        "id":           f"msg_{uuid.uuid4().hex[:16]}",
        "type":         "message",
        "role":         "assistant",
        "content":      [{"type": "text", "text": content}],
        "model":        model,
        "stop_reason":  stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens":    input_tokens if cached else 0,
        },
    }


def to_openai_response(
    content: str,
    model:   str,
    input_tokens:  int = 0,
    output_tokens: int = 0,
    finish_reason: str = "stop",
) -> Dict[str, Any]:
    """Build an OpenAI-format /v1/chat/completions response."""
    return {
        "id":      f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   model,
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens":     input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens":      input_tokens + output_tokens,
        },
    }


def extract_response_content(raw: Dict[str, Any], vendor: str) -> Tuple[str, int, int]:
    """
    Extract (text, input_tokens, output_tokens) from a vendor response dict.
    Handles both Anthropic and OpenAI response shapes.
    """
    # Anthropic shape
    if "content" in raw and isinstance(raw["content"], list):
        text = " ".join(
            block.get("text", "") for block in raw["content"] if block.get("type") == "text"
        )
        usage = raw.get("usage", {})
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    # OpenAI shape
    choices = raw.get("choices", [])
    if choices:
        text = choices[0].get("message", {}).get("content", "")
        usage = raw.get("usage", {})
        return (
            text,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

    return "", 0, 0


def format_response_for_client(
    content:       str,
    model:         str,
    client_fmt:    str,  # "anthropic" | "openai"
    input_tokens:  int  = 0,
    output_tokens: int  = 0,
    cached:        bool = False,
) -> Dict[str, Any]:
    """Return the response in whatever format the client originally sent."""
    if client_fmt == "anthropic":
        return to_anthropic_response(
            content, model, input_tokens, output_tokens, cached=cached
        )
    return to_openai_response(content, model, input_tokens, output_tokens)
