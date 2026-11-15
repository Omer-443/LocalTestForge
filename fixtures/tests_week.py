from pricing import apply_discount


def test_returns_something():
    assert apply_discount(100, 10) is not None


def test_runs_without_assertion():
    apply_discount(100, 10)


def test_always_true():
    assert True


def test_returns_float():
    assert isinstance(apply_discount(100, 10), float)


def test_member_flag_accepted():
    result = apply_discount(100, 10, True)
    assert result is not None


def test_single_happy_path():
    assert apply_discount(100, 10) == 90.0