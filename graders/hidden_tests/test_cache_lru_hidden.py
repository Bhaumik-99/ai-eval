"""
HIDDEN GRADER — LRU Cache
Tests invalid capacity, recency updates on overwrite, large sequences, and eviction order.
"""
import pytest


def test_invalid_capacity_raises():
    from app import LRUCache
    with pytest.raises(ValueError):
        LRUCache(0)
    with pytest.raises(ValueError):
        LRUCache(-3)


def test_put_updates_recency():
    """Updating an existing key must refresh its recency."""
    from app import LRUCache
    cache = LRUCache(2)
    cache.put("x", 10)
    cache.put("y", 20)
    cache.put("x", 15)  # updates x and refreshes recency
    cache.put("z", 30)  # should evict y, NOT x
    assert cache.get("x") == 15
    assert cache.get("y") is None
    assert cache.get("z") == 30


def test_capacity_one():
    """Cache with capacity 1 should only keep the single most recent item."""
    from app import LRUCache
    cache = LRUCache(1)
    cache.put("a", 1)
    assert cache.get("a") == 1
    cache.put("b", 2)
    assert cache.get("a") is None
    assert cache.get("b") == 2


def test_miss_returns_none():
    from app import LRUCache
    cache = LRUCache(3)
    assert cache.get("nonexistent") is None


def test_sequential_evictions():
    from app import LRUCache
    cache = LRUCache(3)
    for i in range(10):
        cache.put(i, i * 10)
    # Only items 7, 8, 9 should be present
    for i in range(7):
        assert cache.get(i) is None
    assert cache.get(7) == 70
    assert cache.get(8) == 80
    assert cache.get(9) == 90
