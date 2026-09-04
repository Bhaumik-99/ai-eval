def calculate_discount(price, percentage):
    """Calculate the discounted price.
    
    Args:
        price: The original price
        percentage: The discount percentage (e.g., 10 for 10%)
    
    Returns:
        The discounted price
    """
    # BUG: No validation for invalid percentage values
    # The function does not handle:
    #   - Negative percentages
    #   - Percentages greater than 100
    #   - Negative prices
    discount_amount = price * percentage / 100
    return price - discount_amount
