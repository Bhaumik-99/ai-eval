def calculate_tax(income, tax_rate):
    """Calculate the tax amount from income and tax rate.
    
    Args:
        income: The gross income amount
        tax_rate: The tax rate as a percentage (e.g., 25 for 25%)
    
    Returns:
        The tax amount, rounded to 2 decimal places
    """
    # BUG 1: tax_rate is a percentage (e.g., 25 for 25%) but treated as decimal factor
    #         should be: income * tax_rate / 100
    # BUG 2: No handling for negative income (should raise or return 0)
    # BUG 3: No handling for invalid tax_rate values
    return round(income * tax_rate, 2)
