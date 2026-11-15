"""Human-owned acceptance tests for the mutator. Do not edit."""
import ast
from pathlib import Path

from localtestforge.mutate import generate_mutants

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SOURCE = (FIXTURES / "pricing.py").read_text(encoding="utf-8")


def test_mutants_are_generated_in_volume():
    assert len(generate_mutants(SOURCE)) >= 15


def test_every_mutant_compiles_and_differs_from_the_original():
    original = ast.unparse(ast.parse(SOURCE))
    for m in generate_mutants(SOURCE):
        compile(m.mutated_source, "<mutant>", "exec")
        assert ast.unparse(ast.parse(m.mutated_source)) != original


def test_ids_are_unique_and_generation_is_deterministic():
    a, b = generate_mutants(SOURCE), generate_mutants(SOURCE)
    assert [m.id for m in a] == [m.id for m in b]
    assert len({m.id for m in a}) == len(a)
    assert [m.mutated_source for m in a] == [m.mutated_source for m in b]


def test_each_mutant_reports_a_real_line_and_a_changed_snippet():
    n_lines = len(SOURCE.splitlines())
    for m in generate_mutants(SOURCE):
        assert 1 <= m.line <= n_lines
        assert m.operator
        assert m.original != m.mutated


def test_expected_operators_appear_on_the_pricing_fixture():
    ops = {m.operator for m in generate_mutants(SOURCE)}
    assert {
        "comparison_flip", "arithmetic_swap", "constant_boundary",
        "min_max_swap", "return_removal",
    } <= ops


def test_boolean_flip_on_inline_source():
    ops = {m.operator for m in generate_mutants("def f(a, b):\n    return a and b\n")}
    assert "boolean_flip" in ops


def test_strings_and_bools_are_not_boundary_mutated():
    src = 'def f():\n    x = True\n    return "hello"\n'
    assert not any(m.operator == "constant_boundary" for m in generate_mutants(src))


def test_docstring_text_is_never_mutated():
    for m in generate_mutants(SOURCE):
        assert "Return `price` after applying" in m.mutated_source