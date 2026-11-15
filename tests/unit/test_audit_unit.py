"""Unit tests for audit orchestration and report building."""

from __future__ import annotations

import json
from pathlib import Path

from localtestforge.audit import (
    OPERATOR_HINTS,
    build_report,
    compute_lf_sha256,
    format_audit_output,
)
from localtestforge.runner import RunResult


def test_compute_lf_sha256_normalizes_crlf() -> None:
    crlf_text = "def hello():\r\n    return 42\r\n"
    lf_text = "def hello():\n    return 42\n"
    assert compute_lf_sha256(crlf_text) == compute_lf_sha256(lf_text)


def test_build_report_schema() -> None:
    orig = RunResult(
        status="passed",
        returncode=0,
        duration_s=0.25,
        counts={"tests": 5, "passed": 5, "failures": 0, "errors": 0, "skipped": 0},
        output_tail="5 passed in 0.1s",
    )
    cov = {
        "line_percent": 100.0,
        "branch_percent": 100.0,
        "num_statements": 6,
        "missing_lines": [],
        "num_branches": 4,
        "covered_branches": 4,
        "missing_branches": [],
    }
    mutation = {
        "total": 1,
        "killed": 1,
        "survived": 0,
        "inconclusive": 0,
        "mutants": [
            {
                "id": "M001",
                "line": 2,
                "operator": "comparison_flip",
                "original": "x < low",
                "mutated": "x <= low",
                "outcome": "killed",
                "diff": "--- original\n+++ mutated\n@@ -1 +1 @@\n-if x < low:\n+if x <= low:\n",
            }
        ],
    }
    report = build_report(
        run_id="20261115_120000_123456",
        started="2026-11-15T12:00:00Z",
        finished="2026-11-15T12:00:01Z",
        env_info={"python": "3.11.5", "platform": "win32", "pytest": "9.2.0", "pytest-cov": "7.1.0"},
        src_path=Path("mini.py"),
        src_hash="abcd1234",
        tests_path=Path("tests_mini.py"),
        tests_hash="ef5678",
        original_run=orig,
        coverage=cov,
        mutation=mutation,
        source_unchanged=True,
    )

    assert report["run_id"] == "20261115_120000_123456"
    assert report["execution_policy"] == "temp workspace + timeout; NOT isolation; trusted code only"
    assert report["source"]["sha256"] == "abcd1234"
    assert report["tests"]["sha256"] == "ef5678"
    assert report["original_run"]["status"] == "passed"
    assert report["coverage"]["line_percent"] == 100.0
    assert report["mutation"]["total"] == 1
    assert report["source_unchanged"] is True

    # Valid JSON serialization check
    serialized = json.dumps(report)
    deserialized = json.loads(serialized)
    assert deserialized == report


def test_operator_hints_contains_all_operators() -> None:
    expected = {
        "comparison_flip": "no test sits exactly on this boundary",
        "constant_boundary": "no test pins this constant's exact value",
        "arithmetic_swap": "no test depends on this calculation",
        "boolean_flip": "no test separates the 'and' from the 'or' case",
        "min_max_swap": "no test distinguishes min from max here",
        "return_removal": "no test checks this return value",
    }
    assert OPERATOR_HINTS == expected


def test_format_audit_output_with_survivors() -> None:
    source_text = "def check(price):\n    if price < 0:\n        return False\n    return price > 10\n"
    report = {
        "original_run": {"status": "passed"},
        "coverage": {"line_percent": 80.0, "branch_percent": 75.0},
        "mutation": {
            "total": 3,
            "killed": 1,
            "survived": 2,
            "inconclusive": 0,
            "mutants": [
                {
                    "id": "M001",
                    "line": 4,
                    "operator": "comparison_flip",
                    "original": "price > 10",
                    "mutated": "price >= 10",
                    "outcome": "survived",
                    "diff": "--- original\n+++ mutated\n@@ -1 +1 @@\n-return price > 10\n+return price >= 10\n",
                },
                {
                    "id": "M002",
                    "line": 2,
                    "operator": "comparison_flip",
                    "original": "price < 0",
                    "mutated": "price <= 0",
                    "outcome": "survived",
                    "diff": "--- original\n+++ mutated\n@@ -1 +1 @@\n-if price < 0:\n+if price <= 0:\n",
                },
                {
                    "id": "M003",
                    "line": 3,
                    "operator": "return_removal",
                    "original": "return False",
                    "mutated": "return None",
                    "outcome": "killed",
                    "diff": "",
                },
            ],
        },
    }
    output = format_audit_output(report, source_text, verbose=False)
    lines = output.splitlines()

    # 1. First line
    assert lines[0] == "Note: audit runs test code without isolation. Trusted code only."
    # 2. Summary lines
    assert lines[1] == "Original run: passed"
    assert lines[2] == "Coverage: 80.0% lines, 75.0% branches"
    assert "Mutants killed: 1/3 (33%), 2 survived" in lines[3]
    # 3. Surviving mutants grouped by source line in ascending order
    assert lines[4] == "Surviving mutants (2), grouped by source line:"
    # Line 2 comes before line 4
    assert lines[5] == "line 2: if price < 0:"
    assert lines[6] == "  M002 comparison_flip: price < 0 -> price <= 0"
    assert lines[7] == "    likely gap: no test sits exactly on this boundary"
    assert lines[8] == "line 4: return price > 10"
    assert lines[9] == "  M001 comparison_flip: price > 10 -> price >= 10"
    assert lines[10] == "    likely gap: no test sits exactly on this boundary"
    # Diffs should not be printed when verbose=False
    assert "--- original" not in output


def test_format_audit_output_verbose() -> None:
    source_text = "def check(x):\n    return x + 1\n"
    report = {
        "original_run": {"status": "passed"},
        "coverage": {"line_percent": 100.0, "branch_percent": 100.0},
        "mutation": {
            "total": 1,
            "killed": 0,
            "survived": 1,
            "inconclusive": 0,
            "mutants": [
                {
                    "id": "M001",
                    "line": 2,
                    "operator": "arithmetic_swap",
                    "original": "x + 1",
                    "mutated": "x - 1",
                    "outcome": "survived",
                    "diff": "--- original\n+++ mutated\n@@ -1 +1 @@\n-return x + 1\n+return x - 1\n",
                }
            ],
        },
    }
    output = format_audit_output(report, source_text, verbose=True)
    assert "--- original" in output
    assert "-return x + 1" in output
    assert "+return x - 1" in output


def test_format_audit_output_all_killed() -> None:
    source_text = "def f(x):\n    return x\n"
    report = {
        "original_run": {"status": "passed"},
        "coverage": {"line_percent": 100.0, "branch_percent": 100.0},
        "mutation": {
            "total": 5,
            "killed": 5,
            "survived": 0,
            "inconclusive": 0,
            "mutants": [],
        },
    }
    output = format_audit_output(report, source_text)
    assert "Mutants killed: 5/5 (100%)" in output
    assert "All 5 mutants killed." in output
    assert "Surviving mutants" not in output


def test_format_audit_output_mutation_skipped() -> None:
    source_text = "def f(x):\n    return x\n"
    report = {
        "original_run": {"status": "test_failures"},
        "coverage": {"line_percent": 50.0, "branch_percent": 50.0},
        "mutation": {
            "skipped": "Original test suite did not pass (status: test_failures)"
        },
    }
    output = format_audit_output(report, source_text)
    assert "Note: audit runs test code without isolation. Trusted code only." in output
    assert "Original run: test_failures" in output
    assert "Coverage: 50.0% lines, 50.0% branches" in output
    assert "Mutation: skipped (test_failures)" in output
    assert "Surviving mutants" not in output

