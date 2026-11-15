"""Audit orchestration for LocalTestForge (SPEC R2-R4)."""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from localtestforge.mutate import generate_mutants
from localtestforge.runner import (
    RunResult,
    classify_outcome,
    run_in_temp_dir,
)

__all__ = [
    "OPERATOR_HINTS",
    "build_report",
    "compute_lf_sha256",
    "format_audit_output",
    "run_audit",
]

OPERATOR_HINTS: dict[str, str] = {
    "comparison_flip": "no test sits exactly on this boundary",
    "constant_boundary": "no test pins this constant's exact value",
    "arithmetic_swap": "no test depends on this calculation",
    "boolean_flip": "no test separates the 'and' from the 'or' case",
    "min_max_swap": "no test distinguishes min from max here",
    "return_removal": "no test checks this return value",
}


def compute_lf_sha256(text: str) -> str:
    """Compute sha256 of LF-normalized text."""
    lf_text = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(lf_text.encode("utf-8")).hexdigest()


def get_environment_info() -> dict[str, str]:
    """Return dictionary with environment versions."""
    import pytest
    import pytest_cov

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pytest": pytest.__version__,
        "pytest-cov": pytest_cov.__version__,
        "pytest_cov": pytest_cov.__version__,
    }


def create_unique_run_dir(out_dir: Path) -> tuple[str, Path]:
    """Create a unique run directory under out_dir, never overwriting existing runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        run_dir = out_dir / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            return run_id, run_dir
        except FileExistsError:
            time.sleep(0.001)


def build_report(
    run_id: str,
    started: str,
    finished: str,
    env_info: dict[str, str],
    src_path: Path,
    src_hash: str,
    tests_path: Path,
    tests_hash: str,
    original_run: RunResult,
    coverage: dict[str, Any],
    mutation: dict[str, Any],
    source_unchanged: bool,
) -> dict[str, Any]:
    """Build the report dictionary conforming to the SPEC schema."""
    return {
        "run_id": run_id,
        "started": started,
        "finished": finished,
        "environment": env_info,
        "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
        "source": {
            "path": str(src_path),
            "sha256": src_hash,
        },
        "tests": {
            "path": str(tests_path),
            "sha256": tests_hash,
        },
        "original_run": {
            "status": original_run.status,
            "counts": original_run.counts,
            "duration_s": original_run.duration_s,
            "output_tail": original_run.output_tail,
        },
        "coverage": coverage,
        "mutation": mutation,
        "source_unchanged": source_unchanged,
    }


def format_audit_output(
    report: dict[str, Any],
    source_text: str,
    verbose: bool = False,
) -> str:
    """Format audit report into plain text for terminal output."""
    lines: list[str] = [
        "Note: audit runs test code without isolation. Trusted code only.",
    ]

    orig_status = report.get("original_run", {}).get("status", "unknown")
    lines.append(f"Original run: {orig_status}")

    cov = report.get("coverage", {})
    line_pct = cov.get("line_percent", 0.0)
    branch_pct = cov.get("branch_percent", 0.0)
    lines.append(f"Coverage: {line_pct}% lines, {branch_pct}% branches")

    mutation = report.get("mutation", {})
    if "skipped" in mutation:
        lines.append(f"Mutation: skipped ({orig_status})")
        return "\n".join(lines)

    total = mutation.get("total", 0)
    killed = mutation.get("killed", 0)
    survived = mutation.get("survived", 0)
    pct = round((killed / total) * 100) if total > 0 else 0

    if total > 0 and killed == total:
        lines.append(f"Mutants killed: {killed}/{total} ({pct}%)")
        lines.append(f"All {total} mutants killed.")
        return "\n".join(lines)

    if total == 0:
        lines.append("All 0 mutants killed.")
        return "\n".join(lines)

    summary_killed = f"Mutants killed: {killed}/{total} ({pct}%)"
    if survived > 0:
        summary_killed += f", {survived} survived"
    lines.append(summary_killed)

    survivors = [m for m in mutation.get("mutants", []) if m.get("outcome") == "survived"]
    if survivors:
        lines.append(f"Surviving mutants ({len(survivors)}), grouped by source line:")
        by_line: dict[int, list[dict[str, Any]]] = {}
        for s in survivors:
            by_line.setdefault(s.get("line", 0), []).append(s)

        src_lines = source_text.splitlines()
        for line_no in sorted(by_line.keys()):
            idx = line_no - 1
            src_snippet = src_lines[idx].strip() if 0 <= idx < len(src_lines) else ""
            lines.append(f"line {line_no}: {src_snippet}")
            for m in by_line[line_no]:
                m_id = m.get("id", "")
                op = m.get("operator", "")
                orig = m.get("original", "")
                mut = m.get("mutated", "")
                lines.append(f"  {m_id} {op}: {orig} -> {mut}")
                hint = OPERATOR_HINTS.get(op, "no test covers this mutant")
                lines.append(f"    likely gap: {hint}")
                if verbose and m.get("diff"):
                    lines.append(m["diff"].rstrip())

    return "\n".join(lines)


def run_audit(
    src: Path,
    tests: Path,
    out_dir: Path = Path("artifacts"),
    timeout: float = 30.0,
    verbose: bool = False,
) -> int:
    """Execute test audit: input validation, original test run, coverage and mutation testing.

    Exit 0 = audit completed (survivors do not change the exit code), 2 = could not audit.
    """
    # 1. Validate inputs
    if not src.exists() or not src.is_file():
        typer.echo(f"error: {src}: file not found", err=True)
        return 2

    if not tests.exists() or not tests.is_file():
        typer.echo(f"error: {tests}: file not found", err=True)
        return 2

    try:
        src_text = src.read_text(encoding="utf-8")
        ast.parse(src_text, filename=str(src))
    except (SyntaxError, UnicodeDecodeError) as exc:
        typer.echo(f"error: {src}: {exc}", err=True)
        return 2

    try:
        tests_text = tests.read_text(encoding="utf-8")
        ast.parse(tests_text, filename=str(tests))
    except (SyntaxError, UnicodeDecodeError) as exc:
        typer.echo(f"error: {tests}: {exc}", err=True)
        return 2

    started_iso = datetime.now(UTC).isoformat()
    src_hash_before = compute_lf_sha256(src_text)
    tests_hash = compute_lf_sha256(tests_text)
    env_info = get_environment_info()
    stem = src.stem

    # 2. Run the suite once with coverage on the ORIGINAL
    orig_result, orig_cov = run_in_temp_dir(
        source_code=src_text,
        test_code=tests_text,
        stem=stem,
        test_filename=tests.name,
        with_cov=True,
        timeout=timeout,
    )

    cov_data: dict[str, Any] = orig_cov if orig_cov is not None else {
        "line_percent": 0.0,
        "branch_percent": 0.0,
        "num_statements": 0,
        "missing_lines": [],
        "num_branches": 0,
        "covered_branches": 0,
        "missing_branches": [],
    }

    # 3. If it does not pass (any failure, error, timeout, or zero tests), write report and exit 2
    if orig_result.status != "passed" or orig_result.counts.get("tests", 0) == 0:
        finished_iso = datetime.now(UTC).isoformat()
        src_hash_after = compute_lf_sha256(src.read_text(encoding="utf-8"))
        source_unchanged = (src_hash_before == src_hash_after)

        mutation_skipped: dict[str, Any] = {
            "skipped": f"Original test suite did not pass (status: {orig_result.status})"
        }

        run_id, run_dir = create_unique_run_dir(out_dir)
        report = build_report(
            run_id=run_id,
            started=started_iso,
            finished=finished_iso,
            env_info=env_info,
            src_path=src,
            src_hash=src_hash_before,
            tests_path=tests,
            tests_hash=tests_hash,
            original_run=orig_result,
            coverage=cov_data,
            mutation=mutation_skipped,
            source_unchanged=source_unchanged,
        )

        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

        output = format_audit_output(report=report, source_text=src_text, verbose=verbose)
        typer.echo(output)
        return 2

    # 4. Otherwise run each mutant from generate_mutants() in its own temp dir (no coverage)
    mutants = generate_mutants(src_text)
    mutant_records: list[dict[str, Any]] = []
    killed_count = 0
    survived_count = 0
    inconclusive_count = 0

    for m in mutants:
        m_result, _ = run_in_temp_dir(
            source_code=m.mutated_source,
            test_code=tests_text,
            stem=stem,
            test_filename=tests.name,
            with_cov=False,
            timeout=timeout,
        )
        outcome = classify_outcome(result=m_result)
        if outcome == "killed":
            killed_count += 1
        elif outcome == "survived":
            survived_count += 1
        else:
            inconclusive_count += 1

        mutant_records.append(
            {
                "id": m.id,
                "line": m.line,
                "operator": m.operator,
                "original": m.original,
                "mutated": m.mutated,
                "outcome": outcome,
                "diff": m.diff,
            }
        )

    mutation_result: dict[str, Any] = {
        "total": len(mutant_records),
        "killed": killed_count,
        "survived": survived_count,
        "inconclusive": inconclusive_count,
        "mutants": mutant_records,
    }

    finished_iso = datetime.now(UTC).isoformat()
    src_hash_after = compute_lf_sha256(src.read_text(encoding="utf-8"))
    source_unchanged = (src_hash_before == src_hash_after)

    run_id, run_dir = create_unique_run_dir(out_dir)
    report = build_report(
        run_id=run_id,
        started=started_iso,
        finished=finished_iso,
        env_info=env_info,
        src_path=src,
        src_hash=src_hash_before,
        tests_path=tests,
        tests_hash=tests_hash,
        original_run=orig_result,
        coverage=cov_data,
        mutation=mutation_result,
        source_unchanged=source_unchanged,
    )

    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # 5. Output: terminal summary via typer.echo (plain text)
    output = format_audit_output(report=report, source_text=src_text, verbose=verbose)
    typer.echo(output)
    return 0
