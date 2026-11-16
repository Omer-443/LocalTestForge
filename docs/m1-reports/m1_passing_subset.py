import pytest
from pricing import apply_discount


def test_apply_discount_negative_price():
    with pytest.raises(ValueError):
        apply_discount(-10.0, 25)


def test_apply_discount_invalid_percent():
    with pytest.raises(ValueError):
        apply_discount(100.0, -1)
    with pytest.raises(ValueError):
        apply_discount(100.0, 101)