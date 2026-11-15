import pytest
from pricing import apply_discount


def test_zero_percent_returns_price():
    assert apply_discount(100, 0) == 100.0


def test_regular_discount():
    assert apply_discount(100, 10) == 90.0


def test_discount_capped_at_fifty():
    assert apply_discount(100, 80) == 50.0


def test_cap_boundary_exact():
    assert apply_discount(100, 50) == 50.0


def test_member_bonus_added():
    assert apply_discount(100, 10, True) == 85.0


def test_member_bonus_applied_after_cap():
    assert apply_discount(100, 80, True) == 45.0


def test_zero_price_is_allowed():
    assert apply_discount(0, 10) == 0.0


def test_hundred_percent_is_allowed():
    assert apply_discount(100, 100) == 50.0


def test_rounds_to_two_decimals():
    assert apply_discount(10.01, 33) == 6.71


def test_negative_price_rejected():
    with pytest.raises(ValueError, match="price"):
        apply_discount(-1, 10)


def test_negative_percent_rejected():
    with pytest.raises(ValueError, match="percent"):
        apply_discount(100, -1)


def test_percent_over_hundred_rejected():
    with pytest.raises(ValueError, match="percent"):
        apply_discount(100, 101)