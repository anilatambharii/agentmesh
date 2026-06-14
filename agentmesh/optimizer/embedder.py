"""
Embedding chain for semantic cache similarity.

Falls back gracefully through three tiers based on what's installed:

  Tier 1 — sentence-transformers (all-MiniLM-L6-v2)
    dim=384, threshold=0.78
    Captures meaning, not surface form. Best for paraphrased prompts.

  Tier 2 — word bigram
    dim=512, threshold=0.72
    Faster than char-bigram, better than char-bigram for word-level rewrites.

  Tier 3 — char bigram
    dim=256, threshold=0.88
    Always available (pure stdlib). Catches typo-level variants only.

Usage:
  from agentmesh.optimizer.embedder import get_embedder
  embed, model_name, threshold = get_embedder()
  vec = embed("review this microservices design...")
  sim = cosine(vec1, vec2)   # compare with another embedding
"""

from __future__ import annotations

import hashlib
import math
from typing import Callable, List, Optional, Tuple

import numpy as np

# ── Public type alias ─────────────────────────────────────────────────────────

EmbedFn    = Callable[[str], np.ndarray]
EmbedTuple = Tuple[EmbedFn, str, float]  # (fn, model_name, threshold)

# ── Tier 1: sentence-transformers ─────────────────────────────────────────────

_st_model = None  # lazy singleton

def _load_st() -> Optional[object]:
    global _st_model
    if _st_model is not None:
        return _st_model
    try:
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        return _st_model
    except Exception:
        return None


def _st_embed(text: str) -> np.ndarray:
    model = _load_st()
    vec = model.encode(_truncate(text), normalize_embeddings=True)
    return vec.astype(np.float32)

# ── Tier 2: word bigram ────────────────────────────────────────────────────────

_WORD_DIM = 512

def _word_bigram_embed(text: str) -> np.ndarray:
    text = _truncate(text)
    words = text.lower().split()
    bigrams = set(zip(words, words[1:])) if len(words) > 1 else {(w, w) for w in words}
    vec = np.zeros(_WORD_DIM, dtype=np.float32)
    for bg in bigrams:
        h = int(hashlib.md5(" ".join(bg).encode()).hexdigest(), 16) % _WORD_DIM
        vec[h] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec

# ── Tier 3: char bigram ────────────────────────────────────────────────────────

_CHAR_DIM = 256

def _char_bigram_embed(text: str) -> np.ndarray:
    text = text.lower()
    bigrams = set(zip(text, text[1:])) if len(text) > 1 else {(c, c) for c in text}
    vec = np.zeros(_CHAR_DIM, dtype=np.float32)
    for bg in bigrams:
        h = (ord(bg[0]) * 31 + ord(bg[1])) % _CHAR_DIM
        vec[h] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec

# ── Cosine similarity ─────────────────────────────────────────────────────────

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity in [-1, 1]. sentence-transformers vectors are pre-normalised."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

# ── Public API ────────────────────────────────────────────────────────────────

# Minimum normalised text length to attempt semantic matching.
# Below this, embeddings are noisy ("fix it", "debug") — fall back to exact-only.
MIN_SEMANTIC_CHARS = 20

# Maximum chars fed to the embedder. sentence-transformers truncates at 256 tokens
# (~1024 chars) anyway, but we pre-truncate to avoid wasting time on huge prompts.
MAX_EMBED_CHARS = 4096


def _truncate(text: str) -> str:
    return text[:MAX_EMBED_CHARS] if len(text) > MAX_EMBED_CHARS else text


def get_embedder() -> EmbedTuple:
    """
    Return the best available (embed_fn, model_name, recommended_threshold).

    Call once at startup and cache the result — the sentence-transformers model
    loads in ~1-2 s the first time; subsequent calls are instant.
    """
    if _load_st() is not None:
        return _st_embed, "sentence-transformers/all-MiniLM-L6-v2", 0.70

    # word-bigram always works (numpy is a required dep)
    return _word_bigram_embed, "word-bigram-512", 0.65


def best_similarity(text_a: str, text_b: str) -> Tuple[float, str]:
    """
    Convenience: return (similarity, model_name) for two raw (pre-normalised) texts.
    Uses the best available embedder.
    """
    embed, name, _ = get_embedder()
    return cosine(embed(text_a), embed(text_b)), name
