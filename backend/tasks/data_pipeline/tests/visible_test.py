"""Visible tests for Data Pipeline task."""
from app import clean_records


def test_basic_clean():
    dirty = [
        {"id": 1, "email": "alice@example.com", "amount": "25.00"},
        {"id": 2, "email": "bob@domain.org", "amount": "$50.50"},
    ]
    res = clean_records(dirty)
    assert len(res) == 2
    assert res[0]["amount"] == 25.0
    assert res[0]["status"] == "active"
    assert res[1]["amount"] == 50.5
