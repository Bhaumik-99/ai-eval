"""Visible tests for the Order Calculation task.
These tests are shown to the agent as a hint.
"""
from app import calculate_order_total


def test_basic_order_no_discount():
    """3 widgets × $10 with 0% tax should total $30."""
    items = [{"name": "Widget", "price": 10.0, "quantity": 3}]
    result = calculate_order_total(items, tax_rate=0.0)
    assert result["subtotal"] == 30.0
    assert result["total"] == 30.0
    assert result["discount"] == 0.0
