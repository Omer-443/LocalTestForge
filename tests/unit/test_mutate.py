"""Unit tests for the AST mutator."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from localtestforge.mutate import Mutant, generate_mutants


def test_comparison_flip_single_operators() -> None:
    src = (
        "def check(a, b):\n"
        "    if a < b:\n"
        "        pass\n"
        "    if a <= b:\n"
        "        pass\n"
        "    if a > b:\n"
        "        pass\n"
        "    if a >= b:\n"
        "        pass\n"
        "    if a == b:\n"
        "        pass\n"
        "    if a != b:\n"
        "        pass\n"
    )
    mutants = generate_mutants(src)
    cmp_mutants = [m for m in mutants if m.operator == "comparison_flip"]
    assert len(cmp_mutants) == 6
    mutated_snippets = {m.mutated for m in cmp_mutants}
    assert "a <= b" in mutated_snippets
    assert "a < b" in mutated_snippets
    assert "a >= b" in mutated_snippets
    assert "a > b" in mutated_snippets
    assert "a != b" in mutated_snippets
    assert "a == b" in mutated_snippets


def test_comparison_flip_chained() -> None:
    src = "def in_range(x):\n    return 0 < x <= 10\n"
    mutants = generate_mutants(src)
    cmp_mutants = [m for m in mutants if m.operator == "comparison_flip"]
    assert len(cmp_mutants) == 2
    snippets = [m.mutated for m in cmp_mutants]
    assert "0 <= x <= 10" in snippets
    assert "0 < x < 10" in snippets


def test_arithmetic_swap_binop() -> None:
    src = "def math(a, b):\n    return (a + b) * (a - b) / a\n"
    mutants = generate_mutants(src)
    arith_mutants = [m for m in mutants if m.operator == "arithmetic_swap"]
    assert len(arith_mutants) == 4
    mut_snippets = {m.mutated for m in arith_mutants}
    assert any("a - b" in s for s in mut_snippets)
    assert any("a + b" in s for s in mut_snippets)
    assert any("/" in s for s in mut_snippets)
    assert any("*" in s for s in mut_snippets)


def test_arithmetic_swap_augassign() -> None:
    src = (
        "def modify(x):\n"
        "    x += 1\n"
        "    x -= 2\n"
        "    x *= 3\n"
        "    x /= 4\n"
    )
    mutants = generate_mutants(src)
    arith_mutants = [m for m in mutants if m.operator == "arithmetic_swap"]
    assert len(arith_mutants) == 4
    mut_sources = [m.mutated_source for m in arith_mutants]
    assert any("x -= 1" in s for s in mut_sources)
    assert any("x += 2" in s for s in mut_sources)
    assert any("x /= 3" in s for s in mut_sources)
    assert any("x *= 4" in s for s in mut_sources)


def test_boolean_flip() -> None:
    src = "def logic(a, b, c):\n    return (a and b) or c\n"
    mutants = generate_mutants(src)
    bool_mutants = [m for m in mutants if m.operator == "boolean_flip"]
    assert len(bool_mutants) == 2
    mut_snippets = {m.mutated for m in bool_mutants}
    assert any("or" in s for s in mut_snippets)
    assert any("and" in s for s in mut_snippets)


def test_constant_boundary_int_and_float() -> None:
    src = "def calc():\n    x = 10\n    y = 2.5\n"
    mutants = generate_mutants(src)
    const_mutants = [m for m in mutants if m.operator == "constant_boundary"]
    assert len(const_mutants) == 4
    mutated_snippets = {m.mutated for m in const_mutants}
    assert {"11", "9", "3.5", "1.5"} == mutated_snippets


def test_skip_bool_and_strings_in_constant_boundary() -> None:
    src = "def f():\n    a = True\n    b = False\n    c = 'text'\n    return c\n"
    mutants = generate_mutants(src)
    const_mutants = [m for m in mutants if m.operator == "constant_boundary"]
    assert len(const_mutants) == 0


def test_skip_docstrings() -> None:
    src = (
        '"""Module docstring."""\n'
        'class MyClass:\n'
        '    """Class docstring."""\n'
        '    def my_method(self):\n'
        '        """Method docstring."""\n'
        '        return 1\n'
    )
    mutants = generate_mutants(src)
    for m in mutants:
        assert "Module docstring." in m.mutated_source
        assert "Class docstring." in m.mutated_source
        assert "Method docstring." in m.mutated_source


def test_skip_type_annotations() -> None:
    src = (
        "def annotate(a: int = 10) -> float:\n"
        "    x: int = 20\n"
        "    return a + x\n"
    )
    mutants = generate_mutants(src)
    const_mutants = [m for m in mutants if m.operator == "constant_boundary"]
    mut_snippets = {m.mutated for m in const_mutants}
    assert mut_snippets == {"11", "9", "21", "19"}


def test_min_max_swap() -> None:
    src = "def bounds(a, b):\n    x = min(a, b)\n    y = max(a, b)\n    return x + y\n"
    mutants = generate_mutants(src)
    swap_mutants = [m for m in mutants if m.operator == "min_max_swap"]
    assert len(swap_mutants) == 2
    snippets = {m.mutated for m in swap_mutants}
    assert "max(a, b)" in snippets
    assert "min(a, b)" in snippets


def test_return_removal() -> None:
    src = (
        "def f(x):\n"
        "    if x > 0:\n"
        "        return x * 2\n"
        "    return\n"
    )
    mutants = generate_mutants(src)
    ret_mutants = [m for m in mutants if m.operator == "return_removal"]
    assert len(ret_mutants) == 1
    assert ret_mutants[0].original == "return x * 2"
    assert ret_mutants[0].mutated == "return None"


def test_return_removal_skips_return_none() -> None:
    src = "def f():\n    return None\n"
    mutants = generate_mutants(src)
    ret_mutants = [m for m in mutants if m.operator == "return_removal"]
    assert len(ret_mutants) == 0


def test_determinism_and_id_stability() -> None:
    src = (
        "def compute(a, b):\n"
        "    c = min(a + 1, b - 2)\n"
        "    if c > 0:\n"
        "        return c * 2\n"
        "    return None\n"
    )
    run1 = generate_mutants(src)
    run2 = generate_mutants(src)
    assert len(run1) > 0
    assert [m.id for m in run1] == [m.id for m in run2]
    assert [m.mutated_source for m in run1] == [m.mutated_source for m in run2]
    assert run1[0].id == "M001"
    assert run1[-1].id == f"M{len(run1):03d}"


def test_all_mutants_compile() -> None:
    src = (
        "def logic(x, y):\n"
        "    if x <= 5 and y > 10:\n"
        "        return max(x + 1, y - 1)\n"
        "    return 0\n"
    )
    mutants = generate_mutants(src)
    assert len(mutants) >= 5
    for m in mutants:
        code = compile(m.mutated_source, "<test>", "exec")
        assert code is not None


def test_syntax_error_returns_empty_list() -> None:
    assert generate_mutants("def broken(") == []


def test_mutant_diff_property() -> None:
    src = "def f(x):\n    return x + 1\n"
    mutants = generate_mutants(src)
    assert len(mutants) > 0
    m = mutants[0]
    diff = m.diff
    assert isinstance(diff, str)
    assert "--- original" in diff
    assert "+++ mutated" in diff
    assert "\r" not in diff
    assert diff.endswith("\n")


def test_mutant_is_frozen() -> None:
    m = Mutant(
        id="M001",
        line=1,
        operator="comparison_flip",
        original="a < b",
        mutated="a <= b",
        mutated_source="def f(a, b): return a <= b\n",
    )
    with pytest.raises(FrozenInstanceError):
        m.id = "M002"  # type: ignore[misc]
