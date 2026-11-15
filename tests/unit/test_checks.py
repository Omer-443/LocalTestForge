"""Unit tests for safety checks in checks.py (Decisions v1 item 1)."""

from __future__ import annotations

import textwrap

from localtestforge.checks import check_candidate, is_candidate_safe


def test_clean_candidate_passes() -> None:
    code = textwrap.dedent(
        """
        import pytest
        from pricing import apply_discount

        def test_discount():
            assert apply_discount(100, 10) == 90.0
        """
    )
    assert is_candidate_safe(code)
    assert check_candidate(code) == []


def test_forbidden_import_roots() -> None:
    for mod in ["os", "sys", "subprocess", "socket", "requests", "urllib", "http", "shutil", "ctypes", "importlib", "pathlib"]:
        code = f"import {mod}\ndef test_something(): pass\n"
        assert not is_candidate_safe(code), f"Expected {mod} to be forbidden"
        violations = check_candidate(code)
        assert any(mod in v for v in violations)

        from_code = f"from {mod} import something\ndef test_something(): pass\n"
        assert not is_candidate_safe(from_code)


def test_forbidden_calls() -> None:
    for func in ["open", "eval", "exec", "compile", "__import__", "input"]:
        code = textwrap.dedent(
            f"""
            import pytest
            def test_call():
                {func}("arg")
            """
        )
        violations = check_candidate(code)
        assert any(func in v for v in violations), f"Expected call {func} to be rejected"


def test_forbidden_pytest_skip_xfail_patterns() -> None:
    # calls
    for call in ["pytest.skip", "pytest.xfail", "pytest.importorskip"]:
        code = textwrap.dedent(
            f"""
            import pytest
            def test_skip():
                {call}("reason")
            """
        )
        assert not is_candidate_safe(code)

    # decorators
    for dec in ["@pytest.mark.skip", "@pytest.mark.skipif(True)", "@pytest.mark.xfail"]:
        code = textwrap.dedent(
            f"""
            import pytest
            {dec}
            def test_skip():
                pass
            """
        )
        assert not is_candidate_safe(code)


def test_top_level_code_other_than_imports_and_tests_is_rejected() -> None:
    # Top-level assignment
    code_assign = textwrap.dedent(
        """
        import pytest
        X = 10
        def test_one():
            assert X == 10
        """
    )
    assert not is_candidate_safe(code_assign)
    violations = check_candidate(code_assign)
    assert any("top-level statement" in v for v in violations)

    # Top-level helper function not starting with test_
    code_helper = textwrap.dedent(
        """
        import pytest
        def helper():
            return 1
        def test_one():
            assert helper() == 1
        """
    )
    assert not is_candidate_safe(code_helper)
    violations = check_candidate(code_helper)
    assert any("helper" in v for v in violations)
