import threading
import time


class TokenBucket:
    """Thread-safe Token Bucket Rate Limiter."""

    def __init__(self, capacity: float, refill_rate: float):
        # BUG: Missing input validation for capacity <= 0 or refill_rate <= 0
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        # BUG: Fails to clamp tokens at self.capacity or compute correct refill
        self.tokens += elapsed * self.refill_rate
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens. Returns True if granted, False if rate limited."""
        with self.lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
