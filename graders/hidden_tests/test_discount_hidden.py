"""
HIDDEN GRADER — Discount Calculator
These tests are NOT shown to the agent.
"""
import pytest
import sys
import os


def test_zero_discount():
    """0% discount should return the original price unchanged."""
    from app import calculate_discount
    assert calculate_discount(100, 0) == 100.0


def test_full_discount():
    """100% discount should return 0."""
    from app import calculate_discount
    assert calculate_discount(100, 100) == 0.0


def test_negative_percentage_raises():
    """Negative percentages must raise ValueError."""
    from app import calculate_discount
    with pytest.raises((ValueError, TypeError)):
        calculate_discount(100, -10)


def test_percentage_over_100_raises():
    """Percentages > 100 must raise ValueError."""
    from app import calculate_discount
    with pytest.raises((ValueError, TypeError)):
        calculate_discount(100, 110)


def test_negative_price_raises():
    """Negative prices must raise ValueError."""
    from app import calculate_discount
    with pytest.raises((ValueError, TypeError)):
        calculate_discount(-100, 10)


def test_float_price():
    """Float prices should work correctly."""
    from app import calculate_discount
    result = calculate_discount(99.99, 10)
    assert abs(result - 89.991) < 0.01


def test_float_percentage():
    """Float percentages should work correctly."""
    from app import calculate_discount
    result = calculate_discount(100, 12.5)
    assert abs(result - 87.5) < 0.01


def test_returns_numeric():
    """Result must be a number (int or float)."""
    from app import calculate_discount
    result = calculate_discount(100, 10)
    assert isinstance(result, (int, float))
