"""
Three-Layer Cost Optimizer — vendor-agnostic compound caching strategy.

Ported and extended from llm-cost-optimization (Anil's library), made universal:
  Layer 1 — Exact Match Cache  (SHA-256 key → 0 tokens burned, instant)
  Layer 2 — Semantic Cache     (cosine similarity → 0 tokens burned)
  Layer 3 — Vendor Prompt Cache (native prefix caching per vendor → 10x cheaper reads)

Combined, these three layers achieve 60-94% cost reduction on enterprise workloads
where many calls are similar (agents re-asking similar questions, common system prompts,
repeated FAQ-style queries from Teams/Copilot/Excel AI).

Example:
    optimizer = CostOptimizer(
        exact_cache=True,
        semantic_cache=True,
        prompt_cache=True,
        similarity_threshold=0.92,
    )
    result = optimizer.lookup("Summarize the Q3 report")
    if result.hit:
        return result.response   # free!
    # ... call LLM ...
    optimizer.store("Summarize the Q3 report", response, model="claude-haiku-4-5", tokens=450)
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from agentmesh.optimizer.normalizer import normalize_prompt
from agentmesh.optimizer.embedder import get_embedder, cosine as _cosine_np, MIN_SEMANTIC_CHARS

# Resolve the best available embedder once at import time (loads ST model ~1-2 s).
_EMBED_FN, _EMBED_MODEL, _EMBED_THRESHOLD = get_embedder()


# ── Cache layer enum ──────────────────────────────────────────────────────────

class CacheLayer(str, Enum):
    EXACT    = "exact"     # SHA-256 match  — cheapest, instant
    SEMANTIC = "semantic"  # cosine similarity — embedding match
    MISS     = "miss"      # cache miss — must call LLM


class CachePolicy(str, Enum):
    STABLE    = "stable"     # TTL 24h  — docs, FAQs, code snippets
    DYNAMIC   = "dynamic"    # TTL 1h   — product info, reports
    REAL_TIME = "real_time"  # TTL 5m   — market data, live status


CACHE_TTL: Dict[CachePolicy, int] = {
    CachePolicy.STABLE:    86400,   # 24h
    CachePolicy.DYNAMIC:   3600,    # 1h
    CachePolicy.REAL_TIME: 300,     # 5m
}


# ── Embedding helpers ─────────────────────────────────────────────────────────
# The actual embedding logic lives in agentmesh.optimizer.embedder.
# _EMBED_FN / _EMBED_MODEL / _EMBED_THRESHOLD are resolved at module import.
# Legacy char-bigram kept here only so old serialised CacheEntry objects can
# still be compared if the ST model isn't available.

def _cheap_embedding(text: str, dim: int = 256) -> np.ndarray:
    """Fallback char-bigram embedding (no external deps required)."""
    text = text.lower().strip()
    counts: Dict[str, int] = {}
    for i in range(len(text) - 1):
        gram = text[i: i + 2]
        counts[gram] = counts.get(gram, 0) + 1
    vec = np.zeros(dim, dtype=np.float32)
    for gram, count in counts.items():
        idx = (hash(gram) & 0x7FFFFFFF) % dim
        vec[idx] += count
    norm = float(np.linalg.norm(vec)) or 1.0
    return vec / norm


# ── Cache entries ─────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    key:        str
    response:   Any
    model:      str
    vendor:     str
    tokens:     int
    policy:     CachePolicy
    embedding:  Any  # np.ndarray (sentence-transformers) or List[float] (legacy)
    created_at: float = field(default_factory=time.time)
    hit_count:  int   = 0
    tags:       List[str] = field(default_factory=list)

    @property
    def ttl(self) -> int:
        return CACHE_TTL[self.policy]

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


@dataclass
class CacheLookupResult:
    hit:         bool
    layer:       CacheLayer
    response:    Optional[Any]  = None
    similarity:  float          = 0.0
    tokens_saved: int           = 0
    cost_saved_usd: float       = 0.0
    latency_ms:  float          = 0.0
    cache_key:   str            = ""


# ── Three-Layer Cost Optimizer ────────────────────────────────────────────────

class CostOptimizer:
    """
    Compound three-layer cache for LLM calls — vendor-agnostic.

    Lookup order: Exact Match → Semantic → Miss (call LLM).

    Compatible with any vendor (Anthropic, OpenAI, Google, Azure, Mistral, Cohere).
    For Anthropic calls, also prepares the cache_control header for prompt caching
    (layer 3 — server-side token discount).
    """

    def __init__(
        self,
        exact_cache:          bool                      = True,
        semantic_cache:       bool                      = True,
        prompt_cache:         bool                      = True,
        similarity_threshold: Optional[float]          = None,
        max_entries:          int                       = 50_000,
        default_policy:       CachePolicy               = CachePolicy.DYNAMIC,
        embedder:             Optional[Callable]        = None,
    ):
        self.exact_cache          = exact_cache
        self.semantic_cache       = semantic_cache
        self.prompt_cache_enabled = prompt_cache
        # Use caller-supplied threshold, or the recommended one for the loaded embedder
        self.similarity_threshold = similarity_threshold if similarity_threshold is not None else _EMBED_THRESHOLD
        self.max_entries          = max_entries
        self.default_policy       = default_policy
        self._embedder            = embedder or _EMBED_FN
        self._embed_model         = _EMBED_MODEL

        self._exact:    Dict[str, CacheEntry]  = {}   # key → entry
        self._semantic: List[CacheEntry]       = []   # ordered for embedding search
        self._lock      = threading.Lock()

        # Stats
        self.exact_hits    = 0
        self.semantic_hits = 0
        self.misses        = 0
        self.tokens_saved  = 0
        self.cost_saved    = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def lookup(
        self,
        prompt:  str,
        context: str    = "",
        model:   str    = "",
        policy:  Optional[CachePolicy] = None,
    ) -> CacheLookupResult:
        """
        Try to find a cached response for this prompt.
        Returns CacheLookupResult — check .hit before deciding whether to call LLM.
        """
        t0  = time.monotonic()
        key = self._make_key(prompt, context, model)

        self._evict_expired()

        # Layer 1: Exact match
        if self.exact_cache:
            with self._lock:
                entry = self._exact.get(key)
            if entry and not entry.is_expired:
                with self._lock:
                    entry.hit_count += 1
                    self.exact_hits  += 1
                    self.tokens_saved += entry.tokens
                return CacheLookupResult(
                    hit=True, layer=CacheLayer.EXACT, response=entry.response,
                    similarity=1.0, tokens_saved=entry.tokens,
                    latency_ms=round((time.monotonic() - t0) * 1000, 2),
                    cache_key=key,
                )

        # Layer 2: Semantic match (skip for very short prompts — embeddings are noisy)
        if self.semantic_cache:
            norm_text = self._normalize(prompt, context)
            if len(norm_text) < MIN_SEMANTIC_CHARS:
                with self._lock:
                    self.misses += 1
                return CacheLookupResult(
                    hit=False, layer=CacheLayer.MISS,
                    latency_ms=round((time.monotonic() - t0) * 1000, 2),
                    cache_key=key,
                )
            embedding = self._embedder(norm_text)
            best_score, best_entry = self._find_best_semantic(embedding)
            if best_score >= self.similarity_threshold and best_entry:
                with self._lock:
                    best_entry.hit_count += 1
                    self.semantic_hits   += 1
                    self.tokens_saved    += best_entry.tokens
                return CacheLookupResult(
                    hit=True, layer=CacheLayer.SEMANTIC, response=best_entry.response,
                    similarity=round(best_score, 4), tokens_saved=best_entry.tokens,
                    latency_ms=round((time.monotonic() - t0) * 1000, 2),
                    cache_key=key,
                )

        with self._lock:
            self.misses += 1
        return CacheLookupResult(
            hit=False, layer=CacheLayer.MISS,
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
            cache_key=key,
        )

    def store(
        self,
        prompt:   str,
        response: Any,
        context:  str          = "",
        model:    str          = "",
        vendor:   str          = "unknown",
        tokens:   int          = 0,
        policy:   Optional[CachePolicy] = None,
        tags:     Optional[List[str]] = None,
    ) -> str:
        """Store a response in both exact and semantic caches. Returns cache key."""
        self._evict_expired()
        if len(self._exact) >= self.max_entries:
            self._evict_lru()

        pol = policy or self.default_policy
        key = self._make_key(prompt, context, model)
        normalized = self._normalize(prompt, context)

        with self._lock:
            if key in self._exact:
                return key   # don't overwrite
            entry = CacheEntry(
                key=key, response=response, model=model, vendor=vendor,
                tokens=tokens, policy=pol,
                embedding=self._embedder(normalized),
                tags=tags or [],
            )
            self._exact[key]    = entry
            self._semantic.append(entry)

        return key

    def invalidate(self, prompt: str = "", context: str = "", model: str = "", tag: str = "") -> int:
        """
        Invalidate cache entries.
        - Exact match: provide prompt + context + model
        - Tag-based: provide tag (bulk removal)
        """
        removed = 0
        with self._lock:
            if tag:
                keys_to_remove = [k for k, e in self._exact.items() if tag in e.tags]
                for k in keys_to_remove:
                    del self._exact[k]
                    removed += 1
                self._semantic = [e for e in self._semantic if tag not in e.tags]
            elif prompt:
                key = self._make_key(prompt, context, model)
                if key in self._exact:
                    del self._exact[key]
                    removed += 1
                self._semantic = [e for e in self._semantic if e.key != key]
        return removed

    def clear(self) -> None:
        with self._lock:
            self._exact.clear()
            self._semantic.clear()

    def prepare_prompt_cache_headers(self, vendor: str, system_prompt: str) -> Dict[str, Any]:
        """
        Return vendor-specific headers/kwargs to enable server-side prompt caching.
        Pass the returned dict as kwargs to the LLM SDK call.

        Anthropic: adds cache_control to system message (10x cheaper reads after first call)
        OpenAI:    automatic (no client-side change needed; just include Prefer header)
        Google:    cachedContent API (advanced — returns cache ID to reuse)
        """
        if vendor == "anthropic":
            return {
                "_cache_system": {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            }
        if vendor == "openai":
            # OpenAI handles prompt caching automatically for long prompts
            return {"_cache_hint": "auto"}
        if vendor in ("google", "azure_openai", "mistral", "cohere"):
            return {}
        return {}

    @property
    def hit_rate(self) -> float:
        total = self.exact_hits + self.semantic_hits + self.misses
        return (self.exact_hits + self.semantic_hits) / total if total else 0.0

    @property
    def stats(self) -> Dict[str, Any]:
        total = self.exact_hits + self.semantic_hits + self.misses
        return {
            "exact_hits":        self.exact_hits,
            "semantic_hits":     self.semantic_hits,
            "misses":            self.misses,
            "total":             total,
            "hit_rate":          round(self.hit_rate, 3),
            "exact_rate":        round(self.exact_hits / total, 3) if total else 0,
            "semantic_rate":     round(self.semantic_hits / total, 3) if total else 0,
            "tokens_saved":      self.tokens_saved,
            "cache_size":        len(self._exact),
            "embed_model":       self._embed_model,
            "sim_threshold":     self.similarity_threshold,
        }

    def cost_report(self, cost_per_1m_tokens: float = 3.0) -> Dict[str, Any]:
        """Calculate USD savings from cache hits."""
        saved_usd = (self.tokens_saved / 1_000_000) * cost_per_1m_tokens
        return {
            **self.stats,
            "cost_saved_usd":        round(saved_usd, 4),
            "cost_per_1m_assumption": cost_per_1m_tokens,
        }

    # ── Internals ────────────────────────────────────────────────────────────

    def _make_key(self, prompt: str, context: str, model: str) -> str:
        # Normalise before hashing so "Please summarise X" and "Summarise X" share a key.
        # Model is intentionally excluded: the same question routed to haiku vs sonnet
        # (e.g. by auto-escalation) should share a single cache entry.
        norm_p = normalize_prompt(prompt)
        norm_c = normalize_prompt(context) if context else ""
        raw = json.dumps({"p": norm_p, "c": norm_c}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _normalize(self, prompt: str, context: str = "") -> str:
        """Return a cleaned string ready for embedding."""
        norm_p = normalize_prompt(prompt)
        norm_c = normalize_prompt(context) if context else ""
        return f"{norm_c} {norm_p}".strip() if norm_c else norm_p

    def _find_best_semantic(self, embedding: Any) -> Tuple[float, Optional[CacheEntry]]:
        best_score  = -1.0
        best_entry  = None
        with self._lock:
            entries = list(self._semantic)
        for entry in entries:
            if entry.is_expired:
                continue
            try:
                score = _cosine_np(
                    np.asarray(embedding, dtype=np.float32),
                    np.asarray(entry.embedding, dtype=np.float32),
                )
            except Exception:
                score = -1.0
            if score > best_score:
                best_score = score
                best_entry = entry
        return best_score, best_entry

    def _evict_expired(self) -> None:
        with self._lock:
            expired_keys = [k for k, e in self._exact.items() if e.is_expired]
            for k in expired_keys:
                del self._exact[k]
            self._semantic = [e for e in self._semantic if not e.is_expired]

    def _evict_lru(self) -> None:
        with self._lock:
            if not self._exact:
                return
            # Remove oldest + least-hit entry
            victim = min(self._exact.values(), key=lambda e: (e.hit_count, e.created_at))
            del self._exact[victim.key]
            self._semantic = [e for e in self._semantic if e.key != victim.key]
