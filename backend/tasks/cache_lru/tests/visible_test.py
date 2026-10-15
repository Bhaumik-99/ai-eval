"""Visible tests for LRU Cache."""
from app import LRUCache


def test_basic_lru_operations():
    cache = LRUCache(2)
    cache.put(1, "A")
    cache.put(2, "B")
    assert cache.get(1) == "A"
    cache.put(3, "C")  # should evict key 2 (since key 1 was read recently)
    assert cache.get(2) is None
    assert cache.get(3) == "C"
