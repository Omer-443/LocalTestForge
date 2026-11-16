"""Unit tests for the CLI scaffold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from localtestforge.cli import app, format_quarantine_block
from localtestforge.generate import GenerateOutcome

runner = CliRunner()

EXPECTED_COMMANDS = {"lint", "audit", "generate", "bench"}

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SOURCE = FIXTURES / "pricing.py"


def test_help_exits_zero_and_lists_all_commands() -> None:
    """--help must exit 0 and mention every command name."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"--help exited {result.exit_code}:\n{result.output}"
    for command in EXPECTED_COMMANDS:
        assert command in result.output, f"Command '{command}' not found in --help output:\n{result.output}"


# ---------------------------------------------------------------------------
# format_quarantine_block unit tests
# ---------------------------------------------------------------------------


def test_format_quarantine_block_disagreement_no_evidence() -> None:
    """DISAGREEMENT with no message produces just the header line."""
    rec = {
        "reason": "test_failed",
        "final_name": "test_b001_apply_discount_normal",
        "message": None,
    }
    block = format_quarantine_block(rec)
    assert block == "DISAGREEMENT test_b001_apply_discount_normal"


def test_format_quarantine_block_disagreement_with_evidence() -> None:
    """Up to two E-lines are indented below the header; file-ref line is excluded."""
    msg = (
        "E   assert 35.0 == 37.5\n"
        "E     where 35.0 = apply_discount(50.0, 30.0)\n"
        "test_single.py:7: AssertionError"
    )
    rec = {
        "reason": "test_failed",
        "final_name": "test_b001_apply_discount_normal",
        "message": msg,
    }
    block = format_quarantine_block(rec)
    lines = block.splitlines()
    assert lines[0] == "DISAGREEMENT test_b001_apply_discount_normal"
    assert lines[1] == "  assert 35.0 == 37.5"
    assert lines[2] == "  where 35.0 = apply_discount(50.0, 30.0)"
    assert len(lines) == 3  # file-ref line must be dropped


def test_format_quarantine_block_broken_test() -> None:
    """BROKEN TEST header includes the error type; E-lines are indented."""
    msg = "E   NameError: name 'foo' is not defined"
    rec = {
        "reason": "test_error",
        "error_type": "NameError",
        "final_name": "test_b002_something",
        "message": msg,
    }
    block = format_quarantine_block(rec)
    lines = block.splitlines()
    assert lines[0] == "BROKEN TEST (NameError) test_b002_something"
    assert "NameError" in lines[1]


def test_format_quarantine_block_other_reason() -> None:
    """Non-standard reason uses QUARANTINED prefix."""
    rec = {
        "reason": "timeout",
        "final_name": "test_b003_slow",
        "message": None,
    }
    block = format_quarantine_block(rec)
    assert block.startswith("QUARANTINED (timeout)")


def test_format_quarantine_block_caps_at_two_evidence_lines() -> None:
    """Only the first two E-lines are shown even if more exist."""
    msg = (
        "E   assert 1 == 2\n"
        "E   where 1 = func()\n"
        "E   extra third line\n"
    )
    rec = {"reason": "test_failed", "final_name": "test_b001_x", "message": msg}
    block = format_quarantine_block(rec)
    lines = block.splitlines()
    # header + 2 evidence lines max
    assert len(lines) <= 3


# ---------------------------------------------------------------------------
# CLI integration: isolation note appears exactly once
# ---------------------------------------------------------------------------


def _make_canned_outcome(run_dir: Path) -> GenerateOutcome:
    """Build a GenerateOutcome that looks like a successful generate+audit run."""
    # Write a minimal audit report.json so the CLI can read it.
    audit_dir = run_dir / "audit" / "20260101_000000_000000"
    audit_dir.mkdir(parents=True)
    audit_report = {
        "run_id": "20260101_000000_000000",
        "started": "2026-01-01T00:00:00+00:00",
        "finished": "2026-01-01T00:00:01+00:00",
        "environment": {},
        "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
        "source": {"path": str(SOURCE), "sha256": "abc"},
        "tests": {"path": "candidate_passing.py", "sha256": "def"},
        "original_run": {"status": "passed", "counts": {"tests": 1, "passed": 1}, "duration_s": 0.1, "output_tail": ""},
        "coverage": {"line_percent": 80.0, "branch_percent": 70.0},
        "mutation": {
            "total": 0,
            "killed": 0,
            "survived": 0,
            "inconclusive": 0,
            "mutants": [],
        },
        "source_unchanged": True,
    }
    (audit_dir / "report.json").write_text(json.dumps(audit_report), encoding="utf-8")

    report = {
        "generation": {"counts": {"accepted": 1}},
        "execution": {
            "status": "completed",
            "tests_generated": 1,
            "passed": 1,
            "quarantined": 0,
            "quarantine_reasons": {},
            "tests": [
                {
                    "name": "test_ok",
                    "final_name": "test_b001_ok",
                    "behavior_id": "b001",
                    "outcome": "passed",
                    "reason": None,
                    "message": None,
                }
            ],
        },
        "audit": {
            "tests_used": 1,
            "tests_excluded": 0,
            "coverage": {"line_percent": 80.0, "branch_percent": 70.0},
            "mutation": {"total": 0, "killed": 0, "survived": 0, "inconclusive": 0, "survivors": []},
            "report_path": str(audit_dir / "report.json"),
        },
    }
    return GenerateOutcome(exit_code=0, run_dir=run_dir, report=report)


def test_isolation_note_appears_exactly_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The 'Note: audit runs test code without isolation' line appears exactly once in generate output."""
    canned = _make_canned_outcome(tmp_path)

    monkeypatch.setattr(
        "localtestforge.ollama.OllamaClient.__init__",
        lambda self, **kwargs: None,
    )
    monkeypatch.setattr(
        "localtestforge.generate.run_generate",
        lambda *args, **kwargs: canned,
    )

    result = runner.invoke(
        app,
        ["generate", str(SOURCE), "--approve", "--out", str(tmp_path)],
    )

    note = "Note: audit runs test code without isolation"
    count = result.output.count(note)
    assert count == 1, (
        f"Expected the isolation note exactly once, found {count}.\n"
        f"Full output:\n{result.output}"
    )
