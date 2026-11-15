"""Unit tests for localtestforge.linter.

All test inputs are small inline source snippets — no fixture files are used.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from localtestforge.linter import Finding, lint_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lint(source: str, tmp_path: Path) -> list[Finding]:
    """Write *source* to a temp file and lint it; return the findings."""
    p = tmp_path / "test_sample.py"
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return lint_file(p)


# ---------------------------------------------------------------------------
# LTF001 — is not None / != None
# ---------------------------------------------------------------------------


def test_ltf001_is_not_none(tmp_path: Path) -> None:
    """assert x is not None → flagged as LTF001."""
    findings = _lint(
        """
        def test_foo():
            x = 1
            assert x is not None
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF001"
    assert findings[0].test_name == "test_foo"


def test_ltf001_neq_none(tmp_path: Path) -> None:
    """assert x != None → flagged as LTF001."""
    findings = _lint(
        """
        def test_foo():
            x = 1
            assert x != None
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF001"


# ---------------------------------------------------------------------------
# LTF002 — constant assertion
# ---------------------------------------------------------------------------


def test_ltf002_true(tmp_path: Path) -> None:
    """assert True → flagged as LTF002."""
    findings = _lint(
        """
        def test_foo():
            assert True
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF002"
    assert findings[0].test_name == "test_foo"


def test_ltf002_constant_int(tmp_path: Path) -> None:
    """assert 1 → flagged as LTF002."""
    findings = _lint(
        """
        def test_foo():
            assert 1
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF002"


# ---------------------------------------------------------------------------
# LTF003 — isinstance-only assertion
# ---------------------------------------------------------------------------


def test_ltf003_isinstance(tmp_path: Path) -> None:
    """assert isinstance(x, int) → flagged as LTF003."""
    findings = _lint(
        """
        def test_foo():
            x = 1
            assert isinstance(x, int)
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF003"
    assert findings[0].test_name == "test_foo"


# ---------------------------------------------------------------------------
# LTF004 — no assertion at all
# ---------------------------------------------------------------------------


def test_ltf004_no_assertion(tmp_path: Path) -> None:
    """Test with no assert and no pytest.raises → flagged as LTF004 at def line."""
    findings = _lint(
        """
        def test_foo():
            x = 1 + 1
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF004"
    assert findings[0].test_name == "test_foo"
    # Finding must be reported at the def line, not inside the body
    assert findings[0].line == 2  # 'def test_foo():' is line 2 after dedent


# ---------------------------------------------------------------------------
# Strong cases — must NOT be flagged
# ---------------------------------------------------------------------------


def test_mixed_weak_then_strong_not_flagged(tmp_path: Path) -> None:
    """Weak assert followed by a strong assert → test is NOT flagged."""
    findings = _lint(
        """
        def test_foo():
            x = 5
            assert x is not None   # weak
            assert x == 5          # strong
        """,
        tmp_path,
    )
    assert findings == []


def test_pytest_raises_only_not_flagged(tmp_path: Path) -> None:
    """Only a pytest.raises block → strong, NOT flagged."""
    findings = _lint(
        """
        import pytest

        def test_foo():
            with pytest.raises(ValueError):
                int("bad")
        """,
        tmp_path,
    )
    assert findings == []


def test_strong_equality_assert_not_flagged(tmp_path: Path) -> None:
    """assert x == 5 → strong, NOT flagged."""
    findings = _lint(
        """
        def test_foo():
            assert 1 + 1 == 2
        """,
        tmp_path,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# Test class method discovery
# ---------------------------------------------------------------------------


def test_class_method_flagged(tmp_path: Path) -> None:
    """test_* method inside a Test* class → discovered and flagged."""
    findings = _lint(
        """
        class TestMath:
            def test_always_true(self):
                assert True
        """,
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].rule == "LTF002"
    assert findings[0].test_name == "test_always_true"


def test_non_test_class_ignored(tmp_path: Path) -> None:
    """Methods in a class that does not start with Test* are not discovered."""
    findings = _lint(
        """
        class Helper:
            def test_something(self):
                assert True
        """,
        tmp_path,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# Non-test functions are ignored
# ---------------------------------------------------------------------------


def test_non_test_function_ignored(tmp_path: Path) -> None:
    """A helper function that doesn't start with test_ must not be flagged."""
    findings = _lint(
        """
        def helper():
            assert True
        """,
        tmp_path,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# Syntax error input
# ---------------------------------------------------------------------------


def test_syntax_error_raises(tmp_path: Path) -> None:
    """lint_file must raise SyntaxError on unparseable source."""
    p = tmp_path / "test_bad.py"
    p.write_text("def test_foo(\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        lint_file(p)


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


def test_output_format(tmp_path: Path) -> None:
    """Finding.__str__ must match ``path:line: RULE test_name: message``."""
    # No leading blank line so def is on line 1, assert True is on line 2.
    source = "def test_foo():\n    assert True\n"
    p = tmp_path / "test_sample.py"
    p.write_text(source, encoding="utf-8")
    findings = lint_file(p)
    assert len(findings) == 1
    text = str(findings[0])
    # assert True is on line 2; check format: path:line: RULE test_name: message
    assert ":2: LTF002 test_foo:" in text


# ---------------------------------------------------------------------------
# CLI exit-code tests (via typer.testing.CliRunner)
# ---------------------------------------------------------------------------


def test_cli_exit_0_on_clean_file(tmp_path: Path) -> None:
    """lint of a file with only strong assertions → exit code 0."""
    from typer.testing import CliRunner

    from localtestforge.cli import app

    p = tmp_path / "test_clean.py"
    p.write_text("def test_foo():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["lint", str(p)])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_cli_exit_1_on_findings(tmp_path: Path) -> None:
    """lint of a file with weak assertions → exit code 1."""
    from typer.testing import CliRunner

    from localtestforge.cli import app

    p = tmp_path / "test_weak.py"
    p.write_text("def test_foo():\n    assert True\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["lint", str(p)])
    assert result.exit_code == 1
    assert "LTF002" in result.output


def test_cli_exit_2_on_missing_file(tmp_path: Path) -> None:
    """lint of a non-existent file → exit code 2."""
    from typer.testing import CliRunner

    from localtestforge.cli import app

    result = CliRunner().invoke(app, ["lint", str(tmp_path / "no_such_file.py")])
    assert result.exit_code == 2


def test_cli_exit_2_on_syntax_error(tmp_path: Path) -> None:
    """lint of a file with a syntax error → exit code 2."""
    from typer.testing import CliRunner

    from localtestforge.cli import app

    p = tmp_path / "test_broken.py"
    p.write_text("def test_foo(\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["lint", str(p)])
    assert result.exit_code == 2
