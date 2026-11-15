"""Human-owned acceptance tests for `ltf audit`. Do not edit."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from localtestforge.cli import app

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
runner = CliRunner()
MUTANT_KEYS = {"id", "line", "operator", "original", "mutated", "outcome", "diff"}


def _audit(out_dir: Path, src: str, tests: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(
        app, ["audit", str(FIXTURES / src), str(FIXTURES / tests), "--out", str(out_dir)]
    )
    reports = sorted(out_dir.glob("*/report.json"))
    return result, [json.loads(p.read_text(encoding="utf-8")) for p in reports]


def _line_of(fragment: str) -> int:
    lines = (FIXTURES / "pricing.py").read_text(encoding="utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1) if fragment in line)


@pytest.fixture(scope="module")
def strong(tmp_path_factory):
    result, reports = _audit(tmp_path_factory.mktemp("strong"), "pricing.py", "tests_strong.py")
    assert len(reports) == 1
    return result, reports[0]


@pytest.fixture(scope="module")
def weak(tmp_path_factory):
    result, reports = _audit(tmp_path_factory.mktemp("weak"), "pricing.py", "tests_weak.py")
    assert len(reports) == 1
    return result, reports[0]


def _boundary_mutant(report):
    line = _line_of("if price < 0:")
    found = [
        m for m in report["mutation"]["mutants"]
        if m["line"] == line and m["operator"] == "comparison_flip"
    ]
    assert len(found) == 1
    return found[0]


def test_strong_suite_audit_completes_with_full_coverage(strong):
    result, report = strong
    assert result.exit_code == 0
    assert report["original_run"]["status"] == "passed"
    assert report["coverage"]["line_percent"] == 100.0
    assert report["coverage"]["branch_percent"] == 100.0


def test_strong_suite_kills_nearly_every_mutant(strong):
    _, report = strong
    m = report["mutation"]
    assert m["total"] >= 15
    assert m["killed"] + m["survived"] + m["inconclusive"] == m["total"]
    assert len(m["mutants"]) == m["total"]
    assert m["inconclusive"] == 0
    assert m["survived"] <= 2  # tolerates genuinely equivalent mutants


def test_weak_suite_kills_strictly_fewer(strong, weak):
    s, w = strong[1]["mutation"], weak[1]["mutation"]
    assert w["total"] == s["total"]
    assert w["killed"] < s["killed"]
    assert w["killed"] / w["total"] <= 0.6


def test_price_boundary_mutant_survives_weak_but_is_killed_by_strong(strong, weak):
    assert _boundary_mutant(weak[1])["outcome"] == "survived"
    assert _boundary_mutant(strong[1])["outcome"] == "killed"


def test_every_mutant_is_explained(weak):
    for m in weak[1]["mutation"]["mutants"]:
        assert MUTANT_KEYS <= set(m)
        assert m["outcome"] in {"killed", "survived", "inconclusive"}
    assert "<=" in _boundary_mutant(weak[1])["diff"]


def test_terminal_summary_names_the_survivors(weak):
    result, _ = weak
    assert "survived" in result.output.lower()
    assert f"line {_line_of('if price < 0:')}" in result.output


def test_suite_failing_on_original_is_not_audited(tmp_path):
    result, reports = _audit(tmp_path, "pricing.py", "tests_broken.py")
    assert result.exit_code == 2
    assert len(reports) == 1
    assert reports[0]["original_run"]["status"] == "test_failures"
    assert "skipped" in reports[0]["mutation"]


def test_missing_input_exits_2(tmp_path):
    result = runner.invoke(
        app,
        ["audit", str(FIXTURES / "nope.py"), str(FIXTURES / "tests_strong.py"),
         "--out", str(tmp_path)],
    )
    assert result.exit_code == 2


def test_each_run_gets_a_new_directory_and_source_is_untouched(tmp_path):
    src = FIXTURES / "mini.py"
    before = src.read_bytes()
    for _ in range(2):
        r = runner.invoke(
            app,
            ["audit", str(src), str(FIXTURES / "tests_mini.py"), "--out", str(tmp_path)],
        )
        assert r.exit_code == 0
    reports = sorted(tmp_path.glob("*/report.json"))
    assert len(reports) == 2
    assert len({p.parent.name for p in reports}) == 2
    assert src.read_bytes() == before
    for p in reports:
        assert json.loads(p.read_text(encoding="utf-8"))["source_unchanged"] is True