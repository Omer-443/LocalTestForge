"""Unit tests for code extraction, assembly/renaming, and exit codes."""

from __future__ import annotations

import ast
from pathlib import Path

from localtestforge.generate import (
    extract_code,
    run_generate,
)
from localtestforge.ollama import GenerationResult

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
