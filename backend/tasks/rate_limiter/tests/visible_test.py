"""Visible tests for Rate Limiter task."""
from app import TokenBucket


def test_basic_consumption():
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.consume(1) is True
    assert tb.consume(4) is True
    # Bucket now empty
    assert tb.consume(1) is False
