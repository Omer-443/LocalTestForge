"""Human-owned acceptance test for LTF005. Do not edit."""
import re
from pathlib import Path

from typer.testing import CliRunner

from localtestforge.cli import app

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
runner = CliRunner()


def test_ltf005_flags_misused_raises_even_in_mixed_tests():
    result = runner.invoke(app, ["lint", str(FIXTURES / "tests_broken.py")])
    assert result.exit_code == 1
    assert "LTF005" in result.output
    flagged = set(re.findall(r"\btest_\w+", result.output))
    assert flagged == {"test_misused_raises", "test_mixed_valid_and_misused"}