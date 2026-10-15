from typing import Any, Optional


class LRUCache:
    """Least Recently Used (LRU) Cache."""

    def __init__(self, capacity: int):
        # BUG: Fails to check capacity <= 0
        self.capacity = capacity
        # BUG: Uses plain dict without proper LRU eviction order
        self.cache = {}

    def get(self, key: Any) -> Optional[Any]:
        # BUG: Does not update recency order when item is fetched!
        return self.cache.get(key, None)

    def put(self, key: Any, value: Any) -> None:
        if len(self.cache) >= self.capacity and key not in self.cache:
            # BUG: Removes arbitrary key rather than least recently used
            first_key = next(iter(self.cache))
            del self.cache[first_key]
        self.cache[key] = value
