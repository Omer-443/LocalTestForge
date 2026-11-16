"""Subprocess test runner and result classifier for LocalTestForge."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "RunResult",
    "classify_outcome",
    "classify_run_status",
    "cleanup_temp_dir",
    "parse_coverage",
    "parse_junit",
    "parse_junit_failures",
    "run_in_temp_dir",
]


@dataclass(frozen=True)
class RunResult:
    """Represents the execution result of a pytest run in a workspace."""

    status: str
    returncode: int | None
    duration_s: float
    counts: dict[str, int]
    output_tail: str
    timed_out: bool = False
    failures: tuple[dict[str, str], ...] = ()


def parse_junit_failures(path: Path) -> list[dict[str, str]]:
    """Parse failure and error elements from junit.xml."""
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
        results: list[dict[str, str]] = []
        for tc in root.iter("testcase"):
            tc_name = tc.attrib.get("name", "")
            for child in tc:
                if child.tag in ("failure", "error"):
                    results.append(
                        {
                            "testcase": tc_name,
                            "tag": child.tag,
                            "type": child.attrib.get("type", ""),
                            "message": child.attrib.get("message", ""),
                            "text": child.text or "",
                        }
                    )
        if not results:
            suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
            for s in suites:
                for child in s:
                    if child.tag in ("failure", "error"):
                        results.append(
                            {
                                "testcase": "",
                                "tag": child.tag,
                                "type": child.attrib.get("type", ""),
                                "message": child.attrib.get("message", ""),
                                "text": child.text or "",
                            }
                        )
        return results
    except (ET.ParseError, OSError, ValueError):
        return []


def parse_junit(path: Path) -> dict[str, int]:
    """Parse junit.xml and return counts dictionary."""
    default_counts = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    if not path.exists():
        return default_counts
    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for s in suites:
            for k in tot:
                tot[k] += int(s.attrib.get(k, 0))
        tot["passed"] = max(0, tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"])
        return tot
    except (ET.ParseError, OSError, ValueError, KeyError):
        return default_counts


def parse_coverage(cov_path: Path, module_filename: str) -> dict[str, Any]:
    """Parse coverage.json and return structured coverage dictionary."""
    default_cov: dict[str, Any] = {
        "line_percent": 0.0,
        "branch_percent": 0.0,
        "num_statements": 0,
        "missing_lines": [],
        "num_branches": 0,
        "covered_branches": 0,
        "missing_branches": [],
    }
    if not cov_path.exists():
        return default_cov
    try:
        data = json.loads(cov_path.read_text(encoding="utf-8"))
        entry = next(
            (
                v
                for k, v in data.get("files", {}).items()
                if k == module_filename
                or k.replace("\\", "/").endswith("/" + module_filename)
                or k.replace("\\", "/").endswith(module_filename)
            ),
            None,
        )
        if entry is None:
            return default_cov
        s = entry.get("summary", {})
        num_statements = int(s.get("num_statements", 0))
        covered_lines = int(s.get("covered_lines", 0))
        num_branches = int(s.get("num_branches", 0))
        covered_branches = int(s.get("covered_branches", 0))
        missing_lines = list(entry.get("missing_lines", []))
        missing_branches = list(entry.get("missing_branches", []))

        line_percent = (
            round(100.0 * covered_lines / num_statements, 1)
            if num_statements > 0
            else 100.0
        )
        branch_percent = (
            round(100.0 * covered_branches / num_branches, 1)
            if num_branches > 0
            else 100.0
        )
        return {
            "line_percent": line_percent,
            "branch_percent": branch_percent,
            "num_statements": num_statements,
            "missing_lines": missing_lines,
            "num_branches": num_branches,
            "covered_branches": covered_branches,
            "missing_branches": missing_branches,
        }
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return default_cov


def classify_run_status(returncode: int | None, counts: dict[str, int], timed_out: bool) -> str:
    """Classify the overall status of a pytest run."""
    if timed_out or returncode is None:
        return "timeout"
    if returncode == 0:
        if counts.get("tests", 0) == 0:
            return "no_tests"
        return "passed"
    if returncode == 1:
        if counts.get("tests", 0) == 0:
            return "harness_error"
        return "test_failures"
    if returncode == 2:
        return "collection_error"
    return f"pytest_exit_{returncode}"


def classify_outcome(
    returncode: int | None = None,
    counts: dict[str, int] | None = None,
    *,
    timed_out: bool = False,
    result: Any = None,
) -> str:
    """Classify mutant run outcome as killed, survived, or inconclusive per Decisions v1 item 4."""
    if result is not None:
        if hasattr(result, "timed_out"):
            timed_out = bool(result.timed_out)
        elif isinstance(result, dict):
            timed_out = bool(result.get("timed_out") or result.get("status") == "timeout")
        if hasattr(result, "returncode"):
            returncode = result.returncode
        elif isinstance(result, dict):
            returncode = result.get("returncode")
        if hasattr(result, "counts"):
            counts = result.counts
        elif isinstance(result, dict):
            counts = result.get("counts")

    if timed_out or returncode is None:
        return "inconclusive"

    if counts is None:
        return "inconclusive"

    tests = counts.get("tests", 0)
    failures = counts.get("failures", 0)
    errors = counts.get("errors", 0)
    skipped = counts.get("skipped", 0)
    passed = counts.get("passed", max(0, tests - failures - errors - skipped))

    if returncode == 0:
        if passed > 0:
            return "survived"
        return "inconclusive"

    if returncode == 1:
        if failures > 0 or errors > 0:
            return "killed"
        return "inconclusive"

    return "inconclusive"


def cleanup_temp_dir(path: Path | str, max_retries: int = 5, delay: float = 0.2) -> None:
    """Robust temp-dir cleanup with retries for WinError 32."""
    p = Path(path)
    if not p.exists():
        return
    for attempt in range(max_retries):
        try:
            shutil.rmtree(p)
            return
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                shutil.rmtree(p, ignore_errors=True)


def run_in_temp_dir(
    source_code: str,
    test_code: str,
    stem: str,
    test_filename: str,
    *,
    with_cov: bool = False,
    timeout: float = 30.0,
) -> tuple[RunResult, dict[str, Any] | None]:
    """Execute pytest in an isolated temporary workspace directory.

    Execution policy: temp workspace + timeout; NOT isolation; trusted code only.
    """
    tmp_path = Path(
        os.path.abspath(
            os.path.join(
                os.environ.get("TEMP", os.environ.get("TMP", ".")),
                f"ltf_{uuid.uuid4().hex[:12]}",
            )
        )
    )
    tmp_path.mkdir(parents=True, exist_ok=True)

    try:
        (tmp_path / f"{stem}.py").write_text(source_code, encoding="utf-8")
        (tmp_path / test_filename).write_text(test_code, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(tmp_path)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        coverage_file = tmp_path / f".coverage_{uuid.uuid4().hex}"
        env["COVERAGE_FILE"] = str(coverage_file)

        junit_path = tmp_path / "junit.xml"
        cov_json_path = tmp_path / "coverage.json"

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            test_filename,
            "-p",
            "no:cacheprovider",
            f"--rootdir={tmp_path}",
            f"--junitxml={junit_path}",
        ]
        if with_cov:
            cmd.extend([f"--cov={stem}", "--cov-branch", f"--cov-report=json:{cov_json_path}"])

        timed_out = False
        start_time = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            if sys.platform == "win32":
                with contextlib.suppress(subprocess.SubprocessError, OSError):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        shell=False,
                        check=False,
                        timeout=2.0,
                    )
            with contextlib.suppress(subprocess.SubprocessError, OSError):
                proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=2.0)
            except (subprocess.SubprocessError, OSError):
                stdout, stderr = "", ""
            returncode = proc.returncode

        duration_s = round(time.perf_counter() - start_time, 2)
        counts = parse_junit(junit_path)
        failures = tuple(parse_junit_failures(junit_path))
        status = classify_run_status(returncode, counts, timed_out)

        combined = (stdout or "") + (stderr or "")
        lines = combined.strip().splitlines()
        output_tail = "\n".join(lines[-25:]) if lines else ""

        run_result = RunResult(
            status=status,
            returncode=returncode,
            duration_s=duration_s,
            counts=counts,
            output_tail=output_tail,
            timed_out=timed_out,
            failures=failures,
        )

        cov_data = parse_coverage(cov_json_path, f"{stem}.py") if with_cov else None
        return run_result, cov_data

    finally:
        cleanup_temp_dir(tmp_path)
