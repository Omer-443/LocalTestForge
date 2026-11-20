"""LocalTestForge CLI.

Four commands: lint, audit, generate, bench.
All are placeholders; real logic is not yet implemented.
"""

from __future__ import annotations

import enum
import re
from pathlib import Path
from typing import Annotated, Any

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


def format_quarantine_block(test: dict[str, Any], mode: str = "full") -> str:
    """Format a single quarantined test record into terminal lines.

    Returns a string with the header line and up to two indented evidence lines
    taken from pytest 'E' lines in the recorded message.  The trailing
    'test_single.py:N: ExceptionType' fragment is not included.

    Example output::

        DISAGREEMENT test_b001_apply_discount_normal_execution
          assert 35.0 == 37.5
          where 35.0 = apply_discount(50.0, 30.0)
    """
    reason = test.get("reason", "unknown")
    final_name = test.get("final_name", "")

    if reason == "test_failed":
        if mode == "spec":
            header = f"DISAGREEMENT (spec vs implementation) {final_name}"
        else:
            header = f"DISAGREEMENT {final_name}"
    elif reason == "test_error":
        err_type = test.get("error_type", "Error")
        header = f"BROKEN TEST ({err_type}) {final_name}"
    else:
        header = f"QUARANTINED ({reason}) {final_name}"

    msg = test.get("message") or ""
    evidence_lines: list[str] = []

    if msg:
        if "\n" in msg:
            raw_chunks = msg.splitlines()
        else:
            raw_chunks = re.split(r"(?<=\S)\s+(?=E\s)", msg)

        for chunk in raw_chunks:
            if re.match(r"^\S+\.py:\d+", chunk.strip()):
                continue
            cleaned = re.sub(r"\s*\b\S+\.py:\d+(?::\s*\w+)?", "", chunk).strip()
            if not cleaned:
                continue
            content = re.sub(r"^E(\s+|$)", "", cleaned).strip()
            if content:
                evidence_lines.append(f"  {content}")
            if len(evidence_lines) >= 2:
                break

    parts = [header] + evidence_lines
    return "\n".join(parts)


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
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory where artifacts are written."),
    ] = Path("artifacts"),
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Timeout in seconds for each test run."),
    ] = 30.0,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print diffs for surviving mutants."),
    ] = False,
) -> None:
    """Report line/branch coverage and mutation results with explained survivors."""
    from localtestforge.audit import run_audit

    exit_code = run_audit(
        src=src,
        tests=tests,
        out_dir=out,
        timeout=timeout,
        verbose=verbose,
    )
    raise typer.Exit(exit_code)


@app.command()
def generate(
    src: Annotated[Path, typer.Argument(help="Path to the source file to generate tests for.")],
    mode: Annotated[
        GenerateMode,
        typer.Option("--mode", help="Generation mode: full or spec."),
    ] = GenerateMode.full,
    model: Annotated[
        str,
        typer.Option("--model", help="Ollama model tag."),
    ] = "qwen2.5-coder:1.5b",
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory where artifacts are written."),
    ] = Path("artifacts"),
    approve: Annotated[
        bool,
        typer.Option("--approve", help="Automatically approve running generated tests."),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Timeout in seconds for each test run."),
    ] = 30.0,
    num_predict: Annotated[
        int,
        typer.Option("--num-predict", help="Maximum tokens to generate per behavior."),
    ] = 200,
    temperature: Annotated[
        float,
        typer.Option("--temperature", help="Sampling temperature."),
    ] = 0.0,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Random seed for generation."),
    ] = 42,
    repeat_penalty: Annotated[
        float,
        typer.Option("--repeat-penalty", help="Penalty for repeating tokens."),
    ] = 1.1,
) -> None:
    """Generate a candidate test file using a local Ollama model, then audit it."""
    import json

    from localtestforge.audit import format_audit_output
    from localtestforge.generate import run_generate
    from localtestforge.ollama import OllamaClient

    client = OllamaClient(model=model)
    options = {
        "num_predict": num_predict,
        "temperature": temperature,
        "seed": seed,
        "repeat_penalty": repeat_penalty,
    }

    if approve:
        confirm = lambda _code: True
    else:
        confirm = lambda _code: typer.confirm(
            "Run these generated tests? Trusted code only, no isolation."
        )

    outcome = run_generate(
        source=src,
        client=client,
        out_dir=out,
        confirm=confirm,
        timeout=timeout,
        options=options,
        mode=mode.value,
    )

    rep = outcome.report
    gen = rep.get("generation", {})
    counts = gen.get("counts", {})
    if counts:
        counts_str = ", ".join(f"{cnt} {st}" for st, cnt in sorted(counts.items()))
        typer.echo(f"Behaviors: {counts_str}")

    ex = rep.get("execution", {})
    tests = ex.get("tests", [])
    if tests:
        passed_cnt = ex.get("passed", 0)
        quar_cnt = ex.get("quarantined", 0)
        q_reasons = ex.get("quarantine_reasons", {})
        failed_cnt = q_reasons.get("test_failed", 0)
        broken_cnt = q_reasons.get("test_error", 0)

        parts = [f"{failed_cnt} disagreements", f"{broken_cnt} broken"]
        for r, c in sorted(q_reasons.items()):
            if r not in ("test_failed", "test_error"):
                parts.append(f"{c} {r}")
        typer.echo(f"Tests: {passed_cnt} passed, {quar_cnt} quarantined ({', '.join(parts)})")
        for t in tests:
            if t.get("outcome") == "quarantined":
                typer.echo(format_quarantine_block(t, mode=mode.value))
        if failed_cnt > 0:
            typer.echo(
                "A disagreement may be a bug, an outdated docstring, or a wrong expectation written by the model. Check each one by hand."
            )

    audit_data = rep.get("audit", {})
    if "skipped" in audit_data:
        typer.echo(f"Audit: skipped ({audit_data['skipped']})")
    elif "report_path" in audit_data:
        audit_rep_path = Path(audit_data["report_path"])
        if audit_rep_path.exists():
            audit_json = json.loads(audit_rep_path.read_text(encoding="utf-8"))
            src_text = src.read_text(encoding="utf-8") if src.exists() else ""
            typer.echo(format_audit_output(audit_json, src_text))

    raise typer.Exit(outcome.exit_code)


@app.command()
def bench(
    directory: Annotated[Path, typer.Argument(help="Directory containing functions to benchmark.")],
) -> None:
    """Run generate+audit over many functions and write a comparison table."""
    typer.echo("not implemented yet")
    raise typer.Exit(2)
