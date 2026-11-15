"""Unit tests for behavior enumeration (Decision v2 item 1)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from localtestforge.behaviors import Behavior, enumerate_behaviors

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
