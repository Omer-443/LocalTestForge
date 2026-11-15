"""Unit tests for the CLI scaffold."""

from typer.testing import CliRunner

from localtestforge.cli import app

runner = CliRunner()

EXPECTED_COMMANDS = {"lint", "audit", "generate", "bench"}


def test_help_exits_zero_and_lists_all_commands() -> None:
    """--help must exit 0 and mention every command name."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"--help exited {result.exit_code}:\n{result.output}"
    for command in EXPECTED_COMMANDS:
        assert command in result.output, f"Command '{command}' not found in --help output:\n{result.output}"
