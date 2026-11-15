"""Human-owned acceptance tests for `ltf lint` (R1). Do not edit."""
import re
from pathlib import Path

from typer.testing import CliRunner

from localtestforge.cli import app

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
runner = CliRunner()

WEAK_TESTS = {
    "test_returns_something",
    "test_runs_without_assertion",
    "test_always_true",
    "test_returns_float",
    "test_member_flag_accepted",
}


def _flagged_names(output: str) -> set[str]:
    return set(re.findall(r"\btest_\w+", output))


def test_lint_flags_exactly_the_weak_tests():
    result = runner.invoke(app, ["lint", str(FIXTURES / "tests_weak.py")])
    assert result.exit_code == 1
    assert _flagged_names(result.output) == WEAK_TESTS


def test_lint_is_clean_on_strong_tests():
    result = runner.invoke(app, ["lint", str(FIXTURES / "tests_strong.py")])
    assert result.exit_code == 0
    assert _flagged_names(result.output) == set()