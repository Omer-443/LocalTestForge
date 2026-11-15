import pytest
from pricing import apply_discount


def test_misused_raises():
    assert apply_discount(100.0, -1) == pytest.raises(ValueError)


def test_mixed_valid_and_misused():
    assert apply_discount(100, 10) == 90.0
    assert apply_discount(100.0, 101) == pytest.raises(ValueError)


def test_proper_raises():
    with pytest.raises(ValueError):
        apply_discount(100.0, -1)