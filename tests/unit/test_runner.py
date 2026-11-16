"""Unit tests for the runner and outcome classifier."""

from __future__ import annotations

from pathlib import Path

from localtestforge.runner import (
    RunResult,
    classify_outcome,
    classify_run_status,
    cleanup_temp_dir,
    parse_coverage,
    parse_junit,
    parse_junit_failures,
    run_in_temp_dir,
)


def test_classify_outcome_survived() -> None:
    counts = {"tests": 2, "passed": 2, "failures": 0, "errors": 0, "skipped": 0}
    assert classify_outcome(returncode=0, counts=counts) == "survived"


def test_classify_outcome_killed_by_failure() -> None:
    counts = {"tests": 2, "passed": 1, "failures": 1, "errors": 0, "skipped": 0}
    assert classify_outcome(returncode=1, counts=counts) == "killed"


def test_classify_outcome_killed_by_error() -> None:
    counts = {"tests": 1, "passed": 0, "failures": 0, "errors": 1, "skipped": 0}
    assert classify_outcome(returncode=1, counts=counts) == "killed"


def test_classify_outcome_inconclusive_on_timeout() -> None:
    counts = {"tests": 1, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    assert classify_outcome(returncode=None, counts=counts, timed_out=True) == "inconclusive"


def test_classify_outcome_inconclusive_on_collection_error() -> None:
    counts = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    assert classify_outcome(returncode=2, counts=counts) == "inconclusive"


def test_classify_outcome_inconclusive_on_zero_tests() -> None:
    counts = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    assert classify_outcome(returncode=0, counts=counts) == "inconclusive"


def test_classify_outcome_inconclusive_when_all_skipped() -> None:
    counts = {"tests": 2, "passed": 0, "failures": 0, "errors": 0, "skipped": 2}
    assert classify_outcome(returncode=0, counts=counts) == "inconclusive"


def test_classify_outcome_via_run_result_object() -> None:
    res = RunResult(
        status="test_failures",
        returncode=1,
        duration_s=0.5,
        counts={"tests": 1, "passed": 0, "failures": 1, "errors": 0, "skipped": 0},
        output_tail="FAIL",
    )
    assert classify_outcome(result=res) == "killed"


def test_classify_run_status() -> None:
    assert classify_run_status(0, {"tests": 2}, False) == "passed"
    assert classify_run_status(0, {"tests": 0}, False) == "no_tests"
    assert classify_run_status(1, {"tests": 2}, False) == "test_failures"
    assert classify_run_status(1, {"tests": 0}, False) == "harness_error"
    assert classify_run_status(2, {"tests": 0}, False) == "collection_error"
    assert classify_run_status(None, {}, True) == "timeout"


def test_parse_junit_nonexistent(tmp_path: Path) -> None:
    counts = parse_junit(tmp_path / "missing.xml")
    assert counts == {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}


def test_parse_junit_valid(tmp_path: Path) -> None:
    xml_path = tmp_path / "junit.xml"
    xml_content = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite tests="4" failures="1" errors="0" skipped="1">'
        '</testsuite></testsuites>'
    )
    xml_path.write_text(xml_content, encoding="utf-8")
    counts = parse_junit(xml_path)
    assert counts["tests"] == 4
    assert counts["failures"] == 1
    assert counts["skipped"] == 1
    assert counts["passed"] == 2


def test_parse_junit_failures(tmp_path: Path) -> None:
    assert parse_junit_failures(tmp_path / "missing.xml") == []

    xml_path = tmp_path / "junit.xml"
    xml_content = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite>'
        '<testcase name="test_f">'
        '<failure message="assert 1 == 2" type="AssertionError">traceback</failure>'
        '</testcase>'
        '<testcase name="test_e">'
        '<failure message="NameError: name x is not defined">tb2</failure>'
        '</testcase>'
        '</testsuite></testsuites>'
    )
    xml_path.write_text(xml_content, encoding="utf-8")
    failures = parse_junit_failures(xml_path)
    assert len(failures) == 2
    assert failures[0]["testcase"] == "test_f"
    assert failures[0]["type"] == "AssertionError"
    assert failures[0]["message"] == "assert 1 == 2"
    assert failures[1]["testcase"] == "test_e"
    assert failures[1]["message"] == "NameError: name x is not defined"


def test_parse_coverage_missing_or_empty(tmp_path: Path) -> None:
    cov = parse_coverage(tmp_path / "missing.json", "foo.py")
    assert cov["line_percent"] == 0.0
    assert cov["num_statements"] == 0


def test_run_in_temp_dir_passing_with_coverage() -> None:
    source = "def add(a, b):\n    return a + b\n"
    test_code = "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    res, cov = run_in_temp_dir(
        source_code=source,
        test_code=test_code,
        stem="calc",
        test_filename="test_calc.py",
        with_cov=True,
        timeout=10.0,
    )
    assert res.status == "passed"
    assert res.returncode == 0
    assert res.counts["passed"] == 1
    assert cov is not None
    assert cov["line_percent"] == 100.0


def test_run_in_temp_dir_failing() -> None:
    source = "def add(a, b):\n    return a + b\n"
    test_code = "from calc import add\ndef test_fail():\n    assert add(2, 3) == 99\n"
    res, cov = run_in_temp_dir(
        source_code=source,
        test_code=test_code,
        stem="calc",
        test_filename="test_calc.py",
        with_cov=False,
        timeout=10.0,
    )
    assert res.status == "test_failures"
    assert res.returncode == 1
    assert res.counts["failures"] == 1
    assert cov is None


def test_run_in_temp_dir_timeout() -> None:
    source = "import time\ndef slow():\n    time.sleep(5)\n    return 1\n"
    test_code = "from sleeper import slow\ndef test_slow():\n    assert slow() == 1\n"
    res, _ = run_in_temp_dir(
        source_code=source,
        test_code=test_code,
        stem="sleeper",
        test_filename="test_sleeper.py",
        with_cov=False,
        timeout=0.3,
    )
    assert res.timed_out is True
    assert res.status == "timeout"
    assert classify_outcome(result=res) == "inconclusive"


def test_cleanup_temp_dir(tmp_path: Path) -> None:
    target = tmp_path / "sub"
    target.mkdir()
    (target / "file.txt").write_text("hello", encoding="utf-8")
    cleanup_temp_dir(target)
    assert not target.exists()
