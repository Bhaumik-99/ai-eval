import re
from typing import Dict, List


def clean_records(records: List[Dict]) -> List[Dict]:
    """Clean and standardize input records."""
    cleaned = []
    # BUG: Fails to deduplicate records keeping the latest entry!
    for r in records:
        rec = dict(r)
        # BUG: Fails to parse '$' prefix or handle whitespace in amount
        if "amount" in rec:
            rec["amount"] = float(rec["amount"])
        # BUG: Fails to drop records with invalid email
        if "status" not in rec:
            rec["status"] = "active"
        cleaned.append(rec)
    return cleaned
