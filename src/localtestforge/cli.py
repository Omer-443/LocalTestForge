"""LocalTestForge CLI.

Four commands: lint, audit, generate, bench.
All are placeholders; real logic is not yet implemented.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="ltf",
    help="Evidence-first auditor and generator for pytest tests.",
    no_args_is_help=True,
)


class GenerateMode(str, enum.Enum):
    """Generation mode for the `generate` command."""

    full = "full"
    spec = "spec"


@app.command()
def lint(
    tests: Annotated[Path, typer.Argument(help="Path to the test file to lint.")],
) -> None:
    """Flag weak assertions in a pytest test file via AST analysis."""
    from localtestforge.linter import lint_file

    if not tests.exists():
        typer.echo(f"error: {tests}: file not found", err=True)
        raise typer.Exit(2)
    try:
        findings = lint_file(tests)
    except SyntaxError as exc:
        typer.echo(f"error: {tests}: {exc}", err=True)
        raise typer.Exit(2) from None
    for finding in findings:
        typer.echo(str(finding))
    raise typer.Exit(1 if findings else 0)


@app.command()
def audit(
    src: Annotated[Path, typer.Argument(help="Path to the source file under audit.")],
    tests: Annotated[Path, typer.Argument(help="Path to the test file to audit against.")],
) -> None:
    """Report line/branch coverage and mutation results with explained survivors."""
    typer.echo("not implemented yet")
    raise typer.Exit(2)


@app.command()
def generate(
    src: Annotated[Path, typer.Argument(help="Path to the source file to generate tests for.")],
    mode: Annotated[
        GenerateMode,
        typer.Option("--mode", help="Generation mode: full or spec."),
    ] = GenerateMode.full,
) -> None:
    """Generate a candidate test file using a local Ollama model, then audit it."""
    typer.echo("not implemented yet")
    raise typer.Exit(2)


@app.command()
def bench(
    directory: Annotated[Path, typer.Argument(help="Directory containing functions to benchmark.")],
) -> None:
    """Run generate+audit over many functions and write a comparison table."""
    typer.echo("not implemented yet")
    raise typer.Exit(2)
