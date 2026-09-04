"""
HIDDEN GRADER — Order Total Calculation
These tests are NOT shown to the agent.
"""
import pytest


def test_discount_10_percent_correct():
    """SAVE10 (10% off) on $100 item should deduct exactly $10."""
    from app import calculate_order_total
    items = [{"name": "Widget", "price": 100.0, "quantity": 1}]
    result = calculate_order_total(items, tax_rate=0.0, discount_code="SAVE10")
    assert abs(result["discount"] - 10.0) < 0.01, f"Expected $10 discount, got {result['discount']}"
    assert abs(result["total"] - 90.0) < 0.01


def test_discount_20_percent_correct():
    """SAVE20 (20% off) on $100 item should deduct exactly $20."""
    from app import calculate_order_total
    items = [{"name": "Widget", "price": 100.0, "quantity": 1}]
    result = calculate_order_total(items, tax_rate=0.0, discount_code="SAVE20")
    assert abs(result["discount"] - 20.0) < 0.01
    assert abs(result["total"] - 80.0) < 0.01


def test_halfoff_discount():
    """HALFOFF (50% off) on $200 item should deduct exactly $100."""
    from app import calculate_order_total
    items = [{"name": "Item", "price": 200.0, "quantity": 1}]
    result = calculate_order_total(items, tax_rate=0.0, discount_code="HALFOFF")
    assert abs(result["discount"] - 100.0) < 0.01
    assert abs(result["total"] - 100.0) < 0.01


def test_invalid_code_no_discount():
    """Unknown discount code must produce zero discount."""
    from app import calculate_order_total
    items = [{"name": "Widget", "price": 100.0, "quantity": 1}]
    result = calculate_order_total(items, tax_rate=0.0, discount_code="FAKE99")
    assert result["discount"] == 0.0
    assert result["total"] == 100.0


# --- REGRESSION TESTS ---

def test_regression_no_discount_with_tax():
    """REGRESSION: orders without discount and with tax must still calculate correctly."""
    from app import calculate_order_total
    items = [{"name": "Widget", "price": 50.0, "quantity": 2}]
    result = calculate_order_total(items, tax_rate=8.0)
    assert result["subtotal"] == 100.0
    assert abs(result["tax"] - 8.0) < 0.01
    assert result["discount"] == 0.0
    assert abs(result["total"] - 108.0) < 0.01


def test_regression_multiple_items_no_discount():
    """REGRESSION: multi-item orders without discount must total correctly."""
    from app import calculate_order_total
    items = [
        {"name": "A", "price": 10.0, "quantity": 2},
        {"name": "B", "price": 5.0, "quantity": 4},
    ]
    result = calculate_order_total(items, tax_rate=0.0)
    assert result["subtotal"] == 40.0
    assert result["total"] == 40.0


def test_tax_applied_to_post_discount_amount():
    """Tax must be calculated on the discounted subtotal, not the original."""
    from app import calculate_order_total
    items = [{"name": "X", "price": 100.0, "quantity": 1}]
    result = calculate_order_total(items, tax_rate=10.0, discount_code="SAVE10")
    # Subtotal=100, discount=10, taxable=90, tax=9, total=99
    assert abs(result["total"] - 99.0) < 0.01
