"""Visible tests for the Tax Calculator task.
These tests are shown to the agent as a hint.
"""
from app import calculate_tax


def test_basic_tax():
    """25% tax on $1000 income should be $250.00."""
    result = calculate_tax(1000, 25)
    assert result == 250.0, f"Expected 250.0 but got {result}"
