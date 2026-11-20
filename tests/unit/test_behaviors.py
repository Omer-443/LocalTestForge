"""Unit tests for behavior enumeration (Decision v2 item 1)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from localtestforge.behaviors import (
    Behavior,
    enumerate_behaviors,
    enumerate_spec_behaviors,
    render_function_spec,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_no_params_snippet() -> None:
    code = textwrap.dedent(
        """
        def calculate_total() -> int:
            return 42
        """
    )
    behaviors = enumerate_behaviors(code)
    assert len(behaviors) == 1
    assert behaviors[0] == Behavior(
        id="B001",
        kind="normal",
        description="normal execution of calculate_total",
        function_name="calculate_total",
    )


def test_bool_param_snippet() -> None:
    code = textwrap.dedent(
        """
        def greet(name: str, shout: bool = False) -> str:
            return name.upper() if shout else name
        """
    )
    behaviors = enumerate_behaviors(code)
    # normal + param True + param False
    assert len(behaviors) == 3
    assert behaviors[0].kind == "normal"
    assert behaviors[1] == Behavior(
        id="B002",
        kind="param",
        description="parameter shout is True",
        function_name="greet",
    )
    assert behaviors[2] == Behavior(
        id="B003",
        kind="param",
        description="parameter shout is False",
        function_name="greet",
    )


def test_raise_inside_if_snippet() -> None:
    code = textwrap.dedent(
        """
        def validate_age(age: int) -> int:
            if age < 0:
                raise ValueError("age cannot be negative")
            return age
        """
    )
    behaviors = enumerate_behaviors(code)
    # normal + raises
    assert len(behaviors) == 2
    assert behaviors[0].kind == "normal"
    assert behaviors[1] == Behavior(
        id="B002",
        kind="raises",
        description="raises ValueError when age < 0",
        function_name="validate_age",
    )


def test_nested_ifs_snippet() -> None:
    code = textwrap.dedent(
        """
        def process_coordinates(x: int, y: int) -> int:
            if x > 0:
                if y > 0:
                    return 1
            return 0
        """
    )
    behaviors = enumerate_behaviors(code)
    # 1 normal + 2 branches for outer if + 2 branches for inner if = 5
    assert len(behaviors) == 5
    assert behaviors[0].kind == "normal"
    assert behaviors[1].kind == "branch_true"
    assert behaviors[1].description == "branch true when x > 0"
    assert behaviors[2].kind == "branch_false"
    assert behaviors[2].description == "branch false when x > 0"
    assert behaviors[3].kind == "branch_true"
    assert behaviors[3].description == "branch true when y > 0"
    assert behaviors[4].kind == "branch_false"
    assert behaviors[4].description == "branch false when y > 0"


def test_pricing_fixture_yields_exactly_7_behaviors() -> None:
    source = (FIXTURES / "pricing.py").read_text(encoding="utf-8")
    behaviors = enumerate_behaviors(source)
    assert len(behaviors) == 7
    kinds = [b.kind for b in behaviors]
    assert kinds == ["normal", "param", "param", "raises", "raises", "branch_true", "branch_false"]
    assert [b.id for b in behaviors] == [f"B00{i}" for i in range(1, 8)]


def test_spec_behaviors_docstring_bullets() -> None:
    code = textwrap.dedent(
        """
        def format_user(name: str) -> str:
            \"\"\"Format user display name.

            - Name must be non-empty.
            * Leading and trailing spaces are trimmed.
            Regular line that is not a bullet.
            \"\"\"
            if not name:
                raise ValueError("empty name")
            return name.strip()
        """
    )
    behaviors = enumerate_spec_behaviors(code)
    assert len(behaviors) == 3
    assert behaviors[0] == Behavior(
        id="B001",
        kind="normal",
        description="normal execution of format_user",
        function_name="format_user",
    )
    assert behaviors[1] == Behavior(
        id="B002",
        kind="spec_claim",
        description="docstring claim: Name must be non-empty.",
        function_name="format_user",
    )
    assert behaviors[2] == Behavior(
        id="B003",
        kind="spec_claim",
        description="docstring claim: Leading and trailing spaces are trimmed.",
        function_name="format_user",
    )


def test_spec_behaviors_no_docstring() -> None:
    code = textwrap.dedent(
        """
        def multiply(a: int, b: int) -> int:
            result = a * b
            return result
        """
    )
    warnings: list[str] = []
    behaviors = enumerate_spec_behaviors(code, warnings=warnings)
    assert len(behaviors) == 1
    assert behaviors[0] == Behavior(
        id="B001",
        kind="normal",
        description="normal execution of multiply",
        function_name="multiply",
    )
    assert len(warnings) == 1
    assert "multiply" in warnings[0]


def test_spec_behaviors_bool_params() -> None:
    code = textwrap.dedent(
        """
        def export_data(data: list, is_compact: bool = False, *, dry_run: bool = True) -> str:
            \"\"\"Export data to format.

            - Result is UTF-8 encoded.
            \"\"\"
            if dry_run:
                return "dry"
            return "ok"
        """
    )
    behaviors = enumerate_spec_behaviors(code)
    # 1 normal + 2 for is_compact + 2 for dry_run + 1 bullet claim = 6
    assert len(behaviors) == 6
    kinds = [b.kind for b in behaviors]
    assert kinds == ["normal", "param", "param", "param", "param", "spec_claim"]
    assert behaviors[0].description == "normal execution of export_data"
    assert behaviors[1].description == "parameter is_compact is True"
    assert behaviors[2].description == "parameter is_compact is False"
    assert behaviors[3].description == "parameter dry_run is True"
    assert behaviors[4].description == "parameter dry_run is False"
    assert behaviors[5].description == "docstring claim: Result is UTF-8 encoded."
    # None of the branches from body should leak
    assert not any("dry_run" in b.description and "branch" in b.kind for b in behaviors)


def test_spec_rendering_never_contains_body_text() -> None:
    code = textwrap.dedent(
        """
        def sensitive_calc(price: float, rate: float) -> float:
            \"\"\"Calculate fee from rate.

            - Rate must be positive.
            \"\"\"
            secret_internal_var = "classified_string_literal"
            if price < 0:
                raise ValueError("price must be positive")
            return round(price * rate, 2)
        """
    )
    spec_text = render_function_spec(code)
    # Specification includes signature and docstring
    assert "def sensitive_calc(price: float, rate: float) -> float:" in spec_text
    assert "Calculate fee from rate." in spec_text
    assert "- Rate must be positive." in spec_text

    # Implementation details from body must NEVER appear
    assert "secret_internal_var" not in spec_text
    assert "classified_string_literal" not in spec_text
    assert "price < 0" not in spec_text
    assert "raise ValueError" not in spec_text
    assert "round(price" not in spec_text


def test_spec_rendering_no_docstring_renders_pass() -> None:
    code = textwrap.dedent(
        """
        def do_stuff(x: int) -> int:
            internal_var = "internal_secret"
            return x + 1
        """
    )
    spec_text = render_function_spec(code)
    assert "def do_stuff(x: int) -> int:" in spec_text
    assert "pass" in spec_text
    assert "internal_var" not in spec_text
    assert "internal_secret" not in spec_text

