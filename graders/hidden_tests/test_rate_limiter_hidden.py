"""
HIDDEN GRADER — Token Bucket Rate Limiter
Tests edge cases, negative validation, capacity capping, and concurrent safety.
"""
import pytest
import time
import threading


def test_invalid_capacity_raises():
    from app import TokenBucket
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1.0)
    with pytest.raises(ValueError):
        TokenBucket(capacity=-5, refill_rate=1.0)


def test_invalid_refill_rate_raises():
    from app import TokenBucket
    with pytest.raises(ValueError):
        TokenBucket(capacity=10, refill_rate=0)
    with pytest.raises(ValueError):
        TokenBucket(capacity=10, refill_rate=-2.0)


def test_capacity_cap():
    """Tokens should not exceed capacity after long wait."""
    from app import TokenBucket
    tb = TokenBucket(capacity=5, refill_rate=100.0)
    time.sleep(0.05)
    # Even though refill rate is 100/sec, tokens should be capped at 5
    assert tb.consume(5) is True
    assert tb.consume(1) is False


def test_refill_recovery():
    """Bucket should refill tokens over time."""
    from app import TokenBucket
    tb = TokenBucket(capacity=2, refill_rate=10.0)
    assert tb.consume(2) is True
    assert tb.consume(1) is False
    time.sleep(0.15)  # should refill ~1.5 tokens
    assert tb.consume(1) is True


def test_thread_safety_burst():
    """Concurrent threads attempting consumption must not over-consume."""
    from app import TokenBucket
    capacity = 50
    tb = TokenBucket(capacity=capacity, refill_rate=0.001)  # essentially no refill
    success_count = [0]
    lock = threading.Lock()

    def worker():
        if tb.consume(1):
            with lock:
                success_count[0] += 1

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert success_count[0] == capacity
