from typing import Dict, List, Optional


DISCOUNT_CODES: Dict[str, float] = {
    "SAVE10": 10.0,
    "SAVE20": 20.0,
    "HALFOFF": 50.0,
}


def calculate_order_total(
    items: List[Dict],
    tax_rate: float = 8.0,
    discount_code: Optional[str] = None,
) -> Dict:
    """Calculate the total for an order including tax and optional discount.
    
    Args:
        items: List of items, each with "name", "price" (float), and "quantity" (int)
        tax_rate: Tax percentage to apply (e.g., 8.0 for 8%)
        discount_code: Optional promotional code (SAVE10, SAVE20, HALFOFF)
    
    Returns:
        Dict with keys: subtotal, tax, discount, total (all rounded to 2 decimal places)
    
    BUGS:
        1. discount_pct is a percentage but not divided by 100 — causes 10x over-discount
        2. Tax is applied to the post-discount amount, which is correct, but because
           the discount bug makes discount_amount huge, total becomes negative
    """
    # Calculate subtotal
    subtotal = sum(item["price"] * item["quantity"] for item in items)

    # Apply discount code
    discount_pct = DISCOUNT_CODES.get(discount_code, 0.0) if discount_code else 0.0
    # BUG: should be subtotal * discount_pct / 100
    discount_amount = subtotal * discount_pct

    # Apply tax to discounted amount
    taxable_amount = subtotal - discount_amount
    tax_amount = taxable_amount * tax_rate / 100

    total = taxable_amount + tax_amount

    return {
        "subtotal": round(subtotal, 2),
        "tax": round(tax_amount, 2),
        "discount": round(discount_amount, 2),
        "total": round(total, 2),
    }
