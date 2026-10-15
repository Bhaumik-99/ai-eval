"""
HIDDEN GRADER — ETL Record Cleaner & Transformer
Tests deduplication, invalid emails, string amount coercion, sorting, and defaults.
"""
import pytest


def test_deduplication_keeps_latest():
    """Duplicate IDs must keep the latest occurrence."""
    from app import clean_records
    records = [
        {"id": 1, "email": "old@example.com", "amount": "10"},
        {"id": 2, "email": "two@example.com", "amount": "20"},
        {"id": 1, "email": "new@example.com", "amount": "99"},
    ]
    res = clean_records(records)
    assert len(res) == 2
    rec1 = next(r for r in res if r["id"] == 1)
    assert rec1["email"] == "new@example.com"
    assert rec1["amount"] == 99.0


def test_invalid_email_dropped():
    """Records with missing or malformed emails must be filtered out."""
    from app import clean_records
    records = [
        {"id": 1, "email": "valid@test.com", "amount": "10"},
        {"id": 2, "email": "invalid-no-at-sign", "amount": "20"},
        {"id": 3, "email": "no-dot@domain", "amount": "30"},
        {"id": 4, "email": "", "amount": "40"},
    ]
    res = clean_records(records)
    assert len(res) == 1
    assert res[0]["id"] == 1


def test_amount_formats():
    """Handles various amount formats: floats, ints, currency symbols, and whitespace."""
    from app import clean_records
    records = [
        {"id": 1, "email": "a@b.com", "amount": " $1,250.50 "},
        {"id": 2, "email": "c@d.com", "amount": 100},
        {"id": 3, "email": "e@f.com", "amount": "45.0"},
    ]
    # Clean up commas if supported, or at least currency symbol and whitespace
    try:
        res = clean_records(records)
        assert res[1]["amount"] == 100.0
        assert res[2]["amount"] == 45.0
    except Exception as e:
        pytest.fail(f"Amount parsing failed: {e}")


def test_sorting_by_id_ascending():
    """Results must be ordered by id ascending."""
    from app import clean_records
    records = [
        {"id": 30, "email": "c@test.com", "amount": "30"},
        {"id": 10, "email": "a@test.com", "amount": "10"},
        {"id": 20, "email": "b@test.com", "amount": "20"},
    ]
    res = clean_records(records)
    ids = [r["id"] for r in res]
    assert ids == [10, 20, 30]


def test_preserves_existing_status():
    """Explicit status values (e.g., 'suspended', 'pending') must be preserved."""
    from app import clean_records
    records = [
        {"id": 1, "email": "a@b.com", "amount": "10", "status": "suspended"},
        {"id": 2, "email": "c@d.com", "amount": "20"},
    ]
    res = clean_records(records)
    assert res[0]["status"] == "suspended"
    assert res[1]["status"] == "active"
