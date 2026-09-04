"""
HIDDEN GRADER — Tax Calculator
These tests are NOT shown to the agent.
"""
import pytest


def test_correct_percentage_math():
    """25% of $1000 must be $250.00, not $25000.00."""
    from app import calculate_tax
    result = calculate_tax(1000, 25)
    assert result == 250.0, f"Expected 250.0 (25% of 1000) but got {result}. Did you divide by 100?"


def test_zero_income():
    """Zero income → zero tax."""
    from app import calculate_tax
    assert calculate_tax(0, 25) == 0.0


def test_zero_tax_rate():
    """Zero tax rate → zero tax."""
    from app import calculate_tax
    assert calculate_tax(1000, 0) == 0.0


def test_negative_income_raises_or_zero():
    """Negative income should raise ValueError or return 0."""
    from app import calculate_tax
    try:
        result = calculate_tax(-1000, 25)
        assert result == 0.0, "If not raising, should return 0 for negative income"
    except (ValueError, TypeError):
        pass  # Raising is acceptable


def test_float_income():
    """Float income should produce correctly rounded result."""
    from app import calculate_tax
    result = calculate_tax(1234.56, 20)
    assert abs(result - 246.91) < 0.01


def test_100_percent_tax():
    """100% tax should equal the full income amount."""
    from app import calculate_tax
    assert calculate_tax(1000, 100) == 1000.0


def test_result_rounded_two_decimals():
    """Result should be rounded to at most 2 decimal places."""
    from app import calculate_tax
    result = calculate_tax(333, 33)
    # Verify it's rounded
    assert result == round(result, 2)
