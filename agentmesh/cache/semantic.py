"""
Semantic cache — deduplicate LLM calls using embedding similarity.

Zero external vector-DB dependency: stores embeddings in memory using
numpy cosine similarity. Optional: swap in a real vector store for
production (Pinecone, Weaviate, pgvector) via the custom_store interface.

Typical savings: 10–40% of total LLM spend on repeated/similar queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Pure-Python cosine similarity (no numpy required)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cheap_embedding(text: str, dim: int = 128) -> List[float]:
    """
    Dependency-free character n-gram embedding.

    Not as good as a real embedding model, but sufficient for
    cache hit detection on near-duplicate queries without any
    external calls or packages. Swap in text-embedding-3-small
    via the embedder= parameter for production quality.
    """
    text = text.lower().strip()
    counts: Dict[str, int] = {}
    for i in range(len(text) - 1):
        gram = text[i : i + 2]
        counts[gram] = counts.get(gram, 0) + 1

    vec = [0.0] * dim
    for gram, count in counts.items():
        idx = (hash(gram) & 0x7FFFFFFF) % dim
        vec[idx] += count

    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


@dataclass
class CacheEntry:
    key: str
    prompt_hash: str
    embedding: List[float]
    response: Any
    model: str
    tokens_saved: int
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0


class SemanticCache:
    """
    Embedding-based semantic cache for LLM calls.

    Works out-of-the-box with no external dependencies. Pass a custom
    ``embedder`` callable to use OpenAI / Cohere / local embeddings.

    Args:
        similarity_threshold: Cosine similarity above which a hit is declared (0.70 for sentence-transformers MiniLM; raise for the char-bigram fallback)
        ttl_seconds: Cache entries expire after this duration
        max_entries: Maximum number of entries to retain (LRU eviction)
        embedder: Optional callable (text) -> List[float] for production embeddings

    Example:
        cache = SemanticCache(similarity_threshold=0.70)
        cached = cache.get("What is the capital of France?")
        if cached:
            return cached  # free!
        response = llm.call(prompt)
        cache.put("What is the capital of France?", response, model="haiku")
    """

    def __init__(
        self,
        similarity_threshold: float = 0.70,
        ttl_seconds: int = 3600,
        max_entries: int = 10_000,
        embedder: Optional[Callable[[str], List[float]]] = None,
    ):
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._embedder = embedder or _cheap_embedding
        self._entries: List[CacheEntry] = []

        self.hits = 0
        self.misses = 0
        self.tokens_saved = 0
        self.cost_saved_usd = 0.0

    def get(self, prompt: str, model: Optional[str] = None) -> Optional[Any]:
        """Return a cached response if a semantically similar prompt exists."""
        self._evict_expired()
        if not self._entries:
            self.misses += 1
            return None

        embedding = self._embedder(self._normalize(prompt))
        best_score, best_entry = self._find_best(embedding)

        if best_score >= self.similarity_threshold:
            best_entry.hit_count += 1
            self.hits += 1
            self.tokens_saved += best_entry.tokens_saved
            logger.debug(
                "Cache HIT (score=%.3f, key=%s, hits=%d)",
                best_score, best_entry.key[:8], best_entry.hit_count,
            )
            return best_entry.response

        self.misses += 1
        return None

    def put(
        self,
        prompt: str,
        response: Any,
        model: str = "unknown",
        tokens: int = 0,
        cost_per_1m: float = 3.0,
    ) -> None:
        """Cache a response with its prompt embedding."""
        self._evict_expired()
        if len(self._entries) >= self.max_entries:
            self._evict_lru()

        normalized = self._normalize(prompt)
        key = hashlib.sha256(normalized.encode()).hexdigest()[:16]

        # Don't duplicate exact entries
        if any(e.key == key for e in self._entries):
            return

        entry = CacheEntry(
            key=key,
            prompt_hash=key,
            embedding=self._embedder(normalized),
            response=response,
            model=model,
            tokens_saved=tokens,
        )
        self._entries.append(entry)
        logger.debug("Cache stored entry %s (model=%s, tokens=%d)", key, model, tokens)

    def invalidate(self, prompt: str) -> bool:
        """Invalidate the cache entry for a specific prompt."""
        key = hashlib.sha256(self._normalize(prompt).encode()).hexdigest()[:16]
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.key != key]
        return len(self._entries) < before

    def clear(self) -> None:
        """Clear all cache entries."""
        self._entries.clear()

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 3),
            "size": self.size,
            "tokens_saved": self.tokens_saved,
            "cost_saved_usd": round(self.cost_saved_usd, 4),
        }

    def _normalize(self, text: str) -> str:
        if isinstance(text, list):
            # messages format
            parts = []
            for m in text:
                if isinstance(m, dict):
                    parts.append(str(m.get("content", "")))
                else:
                    parts.append(str(m))
            return " ".join(parts)
        return str(text).strip()

    def _find_best(self, embedding: List[float]) -> Tuple[float, Optional[CacheEntry]]:
        best_score = -1.0
        best_entry = None
        for entry in self._entries:
            score = _cosine_similarity(embedding, entry.embedding)
            if score > best_score:
                best_score = score
                best_entry = entry
        return best_score, best_entry

    def _evict_expired(self) -> None:
        now = time.time()
        self._entries = [e for e in self._entries if now - e.created_at < self.ttl_seconds]

    def _evict_lru(self) -> None:
        if self._entries:
            # Remove the oldest, least-hit entry
            self._entries.sort(key=lambda e: (e.hit_count, e.created_at))
            self._entries.pop(0)
