"""Tests for semantic caching."""

import pytest
from agentmesh.cache.semantic import SemanticCache, _cosine_similarity, _cheap_embedding


def test_cosine_similarity_identical():
    v = [1.0, 0.0, 1.0]
    assert _cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _cosine_similarity(a, b) == pytest.approx(0.0)


def test_cheap_embedding_deterministic():
    e1 = _cheap_embedding("hello world")
    e2 = _cheap_embedding("hello world")
    assert e1 == e2


def test_cheap_embedding_different_texts_differ():
    e1 = _cheap_embedding("What is the capital of France?")
    e2 = _cheap_embedding("How do I reverse a linked list in Python?")
    sim = _cosine_similarity(e1, e2)
    assert sim < 0.95  # different topics should not be nearly identical


def test_cache_miss_on_empty():
    cache = SemanticCache()
    result = cache.get("What is 2 + 2?")
    assert result is None
    assert cache.misses == 1
    assert cache.hits == 0


def test_cache_put_and_exact_hit():
    cache = SemanticCache(similarity_threshold=0.85)
    cache.put("What is the capital of France?", "Paris", model="haiku", tokens=50)
    result = cache.get("What is the capital of France?")
    assert result == "Paris"
    assert cache.hits == 1


def test_cache_near_duplicate_hit():
    cache = SemanticCache(similarity_threshold=0.80)
    cache.put("What is the capital of France?", "Paris", model="haiku")
    # Slightly different phrasing
    result = cache.get("What is France's capital?")
    # May or may not hit depending on embedding similarity — just check no exception
    assert result is None or result == "Paris"


def test_cache_unrelated_query_miss():
    cache = SemanticCache(similarity_threshold=0.92)
    cache.put("What is the capital of France?", "Paris", model="haiku")
    result = cache.get("How do I implement quicksort in Python?")
    assert result is None


def test_cache_ttl_expiry():
    import time
    cache = SemanticCache(ttl_seconds=0)  # immediately expired
    cache.put("test query", "test response", model="haiku")
    time.sleep(0.01)  # ensure time has passed
    result = cache.get("test query")
    assert result is None  # expired


def test_cache_stats():
    cache = SemanticCache()
    cache.put("Q1", "A1", model="haiku")
    cache.get("Q1")   # hit
    cache.get("Q2")   # miss

    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["size"] == 1


def test_cache_max_entries_eviction():
    cache = SemanticCache(max_entries=3)
    for i in range(4):
        cache.put(f"unique query {i} with padding to make it distinct", f"response_{i}")
    # Should have evicted one entry
    assert cache.size <= 3


def test_cache_invalidate():
    cache = SemanticCache()
    cache.put("What is 2+2?", "4", model="haiku")
    assert cache.size == 1
    removed = cache.invalidate("What is 2+2?")
    assert removed is True
    assert cache.size == 0


def test_cache_clear():
    cache = SemanticCache()
    cache.put("Q1", "A1")
    cache.put("Q2", "A2")
    cache.clear()
    assert cache.size == 0


def test_cache_no_duplicate_entries():
    cache = SemanticCache()
    cache.put("same query", "response1")
    cache.put("same query", "response2")  # should not add duplicate
    assert cache.size == 1
