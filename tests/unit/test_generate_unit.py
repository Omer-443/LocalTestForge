"""Unit tests for code extraction, assembly/renaming, and exit codes."""

from __future__ import annotations

import ast
from pathlib import Path

from localtestforge.generate import (
    _trim_to_whole_lines,
    build_deterministic_header,
    classify_quarantine_reason,
    extract_code,
    extract_header_names,
    run_generate,
)
from localtestforge.ollama import GenerationResult
from localtestforge.runner import RunResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SOURCE = FIXTURES / "pricing.py"


def test_extract_code_fenced_and_unclosed() -> None:
    fenced = "```python\ndef test_a():\n    assert True\n```"
    assert extract_code(fenced) == "def test_a():\n    assert True"

    unclosed = "```python\ndef test_b():\n    assert 1 == 1"
    assert extract_code(unclosed) == "def test_b():\n    assert 1 == 1"

    plain = "def test_c():\n    assert False"
    assert extract_code(plain) == "def test_c():\n    assert False"


class DummyClient:
    model = "mock:1b"
    digest = "sha256:mock"

    def __init__(self, response_text: str, done_reason: str = "stop") -> None:
        self.response_text = response_text
        self.done_reason = done_reason

    def generate(self, prompt: str, options: dict) -> GenerationResult:
        return GenerationResult(
            text=self.response_text,
            done_reason=self.done_reason,
            latency_s=0.01,
            eval_count=5,
            tokens_per_s=50.0,
        )


def test_assembly_renaming_and_import_dedupe(tmp_path: Path) -> None:
    code = (
        "```python\n"
        "import pytest\n"
        "from pricing import apply_discount\n"
        "import pytest\n"
        "def test_original():\n"
        "    assert apply_discount(100, 10) == 90.0\n"
        "```"
    )
    client = DummyClient(code)
    outcome = run_generate(SOURCE, client, tmp_path, confirm=lambda _code: True)
    assert outcome.exit_code == 0

    candidate_all = (outcome.run_dir / "candidate_all.py").read_text(encoding="utf-8")
    tree = ast.parse(candidate_all)

    # Check deduplicated imports
    imports = [stmt for stmt in tree.body if isinstance(stmt, (ast.Import, ast.ImportFrom))]
    pytest_imports = [imp for imp in imports if isinstance(imp, ast.Import) and any(n.name == "pytest" for n in imp.names)]
    assert len(pytest_imports) == 1

    # Check renamed test function
    func_names = [stmt.name for stmt in tree.body if isinstance(stmt, ast.FunctionDef)]
    assert func_names[0] == "test_b001_original"


def test_exit_code_missing_source(tmp_path: Path) -> None:
    client = DummyClient("def test_x(): pass")
    missing = tmp_path / "does_not_exist.py"
    outcome = run_generate(missing, client, tmp_path, confirm=lambda _code: True)
    assert outcome.exit_code == 2


def test_exit_code_syntax_error_in_source(tmp_path: Path) -> None:
    bad_source = tmp_path / "bad.py"
    bad_source.write_text("def broken(: pass", encoding="utf-8")
    client = DummyClient("def test_x(): pass")
    outcome = run_generate(bad_source, client, tmp_path, confirm=lambda _code: True)
    assert outcome.exit_code == 2


def test_exit_code_0_when_human_declines(tmp_path: Path) -> None:
    code = "```python\ndef test_ok(): assert 1 == 1\n```"
    client = DummyClient(code)
    outcome = run_generate(SOURCE, client, tmp_path, confirm=lambda _code: False)
    assert outcome.exit_code == 0
    assert outcome.report["execution"]["status"] == "not_run"


def test_exit_code_1_when_no_tests_pass(tmp_path: Path) -> None:
    # All tests fail against pricing.py
    code = "```python\nfrom pricing import apply_discount\ndef test_fail(): assert apply_discount(100, 10) == 0\n```"
    client = DummyClient(code)
    outcome = run_generate(SOURCE, client, tmp_path, confirm=lambda _code: True)
    assert outcome.exit_code == 1
    assert "no passing tests" in outcome.report["audit"]["skipped"].lower()


def test_build_deterministic_header() -> None:
    code = """
def public_one(x): return x
def _private(y): return y
def public_two(z): return z
class IgnoreClass: pass
def public_one(again): return again
"""
    tree = ast.parse(code)
    header = build_deterministic_header(tree, "mymod")
    assert header == [
        "import pytest",
        "from mymod import public_one, public_two",
    ]

    empty_tree = ast.parse("x = 1\n_foo = lambda: None")
    assert build_deterministic_header(empty_tree, "empty") == ["import pytest"]


def test_build_deterministic_header_constants() -> None:
    code = """
_PRIVATE_CONST = 100
low_case = 200
CamelCase = 300
BETA = 2
ALPHA = 1
GAMMA: int = 3
DELTA: str
ALPHA = 99
X = Y = 400
A, B = 500, 600

def get_data(): return 1
def _secret(): return 2
def process_data(): return 3
"""
    tree = ast.parse(code)
    header = build_deterministic_header(tree, "mymod")
    assert header == [
        "import pytest",
        "from mymod import get_data, process_data",
        "from mymod import ALPHA, BETA, DELTA, GAMMA",
    ]


def test_build_deterministic_header_no_constants_means_no_extra_line() -> None:
    code = """
def func_a(): pass
def func_b(): pass
_SECRET = 42
lowercase_var = 10
"""
    tree = ast.parse(code)
    header = build_deterministic_header(tree, "mymod")
    # Function line unchanged, no constants line emitted
    assert header == [
        "import pytest",
        "from mymod import func_a, func_b",
    ]


def test_build_deterministic_header_only_constants() -> None:
    code = """
CONST_B = 2
CONST_A: int = 1
"""
    tree = ast.parse(code)
    header = build_deterministic_header(tree, "mymod")
    assert header == [
        "import pytest",
        "from mymod import CONST_A, CONST_B",
    ]


def test_extract_header_names() -> None:
    code = """
FIRST = 10
_SECOND = 20
third = 30
FOURTH: int = 40

def my_func(): pass
def _priv_func(): pass
"""
    tree = ast.parse(code)
    funcs, consts = extract_header_names(tree)
    assert funcs == ["my_func"]
    assert consts == ["FIRST", "FOURTH"]


def test_classify_quarantine_reason() -> None:
    # Timeout
    res_timeout = RunResult(
        status="timeout", returncode=None, duration_s=1.0, counts={}, output_tail="", timed_out=True
    )
    assert classify_quarantine_reason(res_timeout) == ("timeout", None, "test execution timed out")

    # Collection error
    res_coll = RunResult(
        status="collection_error", returncode=2, duration_s=0.1, counts={}, output_tail="syntax error"
    )
    assert classify_quarantine_reason(res_coll)[0] == "error"

    # AssertionError via type
    res_assert_type = RunResult(
        status="test_failures",
        returncode=1,
        duration_s=0.1,
        counts={"tests": 1, "failures": 1},
        output_tail="E   assert 1 == 2",
        failures=({"tag": "failure", "type": "builtins.AssertionError", "message": "assert 1 == 2", "text": ""},),
    )
    reason, err_type, msg = classify_quarantine_reason(res_assert_type)
    assert reason == "test_failed"
    assert err_type is None
    assert "assert 1 == 2" in msg

    # AssertionError via assert message
    res_assert_msg = RunResult(
        status="test_failures",
        returncode=1,
        duration_s=0.1,
        counts={"tests": 1, "failures": 1},
        output_tail="E   assert 1 == 2",
        failures=({"tag": "failure", "type": "", "message": "assert 1 == 2", "text": ""},),
    )
    assert classify_quarantine_reason(res_assert_msg)[0] == "test_failed"

    # NameError
    res_name_err = RunResult(
        status="test_failures",
        returncode=1,
        duration_s=0.1,
        counts={"tests": 1, "failures": 1},
        output_tail="E   NameError: name 'x' is not defined",
        failures=({"tag": "failure", "type": "", "message": "NameError: name 'x' is not defined", "text": ""},),
    )
    reason, err_type, msg = classify_quarantine_reason(res_name_err)
    assert reason == "test_error"
    assert err_type == "NameError"
    assert "NameError" in msg

    # TypeError via type attribute
    res_type_err = RunResult(
        status="test_failures",
        returncode=1,
        duration_s=0.1,
        counts={"tests": 1, "failures": 1},
        output_tail="E   TypeError: bad",
        failures=({"tag": "failure", "type": "TypeError", "message": "bad", "text": ""},),
    )
    reason, err_type, _ = classify_quarantine_reason(res_type_err)
    assert reason == "test_error"
    assert err_type == "TypeError"

    # Multi-line AssertionError preserving newlines
    res_multiline = RunResult(
        status="test_failures",
        returncode=1,
        duration_s=0.1,
        counts={"tests": 1, "failures": 1},
        output_tail=(
            "E   assert 70.0 == 60.0\n"
            "E        +  where 70.0 = apply_discount(100.0, 30.0)\n"
            "test_single.py:7: AssertionError"
        ),
        failures=({"tag": "failure", "type": "AssertionError", "message": "assert 70.0 == 60.0", "text": ""},),
    )
    reason, err_type, msg = classify_quarantine_reason(res_multiline)
    assert reason == "test_failed"
    assert err_type is None
    assert "\n" in msg
    assert "where 70.0 = apply_discount" in msg


def test_trim_to_whole_lines_preserves_newlines_and_never_cuts_mid_line() -> None:
    lines = ["a" * 100, "b" * 150, "c" * 100]
    trimmed = _trim_to_whole_lines(lines, 300)
    assert trimmed == "a" * 100 + "\n" + "b" * 150
    assert len(trimmed) == 251

    # First line exceeds limit: returned whole without mid-line cut
    single_long = ["x" * 350, "y" * 10]
    assert _trim_to_whole_lines(single_long, 300) == "x" * 350


def test_prompt_version_is_behavior_v2(tmp_path: Path) -> None:
    code = "```python\ndef test_ok(): assert apply_discount(100, 10) == 90.0\n```"
    client = DummyClient(code)
    outcome = run_generate(SOURCE, client, tmp_path, confirm=lambda _code: False)
    assert outcome.report["mode"] == "full"
    assert outcome.report["prompt"]["version"] == "behavior_v2"
    assert outcome.report["prompt"]["template"] == "behavior_v2.txt"
    assert "quarantine_reasons" in outcome.report["execution"]
    assert "disagreements" in outcome.report["execution"]
    assert outcome.report["generation"]["header_names"] == [
        "apply_discount",
        "MAX_DISCOUNT",
        "MEMBER_BONUS",
    ]


def test_spec_mode_report_metadata(tmp_path: Path) -> None:
    code = "```python\ndef test_ok(): assert apply_discount(100, 10) == 90.0\n```"
    client = DummyClient(code)
    outcome = run_generate(SOURCE, client, tmp_path, confirm=lambda _code: False, mode="spec")
    assert outcome.report["mode"] == "spec"
    assert outcome.report["prompt"]["version"] == "spec_v1"
    assert outcome.report["prompt"]["template"] == "spec_v1"
    assert "spec_warnings" in outcome.report["generation"]
    assert "disagreements" in outcome.report["execution"]
    assert outcome.report["generation"]["header_names"] == [
        "apply_discount",
        "MAX_DISCOUNT",
        "MEMBER_BONUS",
    ]

