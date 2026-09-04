"""Visible tests for the Discount Calculator task.
These tests are shown to the agent as a hint.
"""
from app import calculate_discount


def test_basic_discount():
    """10% off $100 should give $90."""
    assert calculate_discount(100, 10) == 90.0


def test_large_discount():
    """25% off $200 should give $150."""
    result = calculate_discount(200, 25)
    assert result == 150.0
