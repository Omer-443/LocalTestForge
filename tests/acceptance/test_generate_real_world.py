"""Human-owned regression tests: real-model failure modes the first fake missed. Do not edit."""
import json
from itertools import cycle
from pathlib import Path

from localtestforge.generate import run_generate
from localtestforge.ollama import GenerationResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SOURCE = FIXTURES / "pricing.py"

NO_IMPORTS = [
    ("```python\ndef test_plain():\n    assert apply_discount(100, 10) == 90.0\n```", "stop"),
    ("```python\ndef test_raises():\n    with pytest.raises(ValueError):\n"
     "        apply_discount(-1, 10)\n```", "stop"),
]
MIXED = [
    ("```python\ndef test_wrong():\n    assert apply_discount(100, 10) == 80.0\n```", "stop"),
    ("```python\ndef test_broken():\n    assert undefined_helper(1) == 2\n```", "stop"),
]


class FakeClient:
    model = "fake:1b"
    digest = "sha256:fake"

    def __init__(self, replies):
        self._replies = cycle(replies)

    def generate(self, prompt, options):
        text, reason = next(self._replies)
        return GenerationResult(
            text=text, done_reason=reason, latency_s=0.01, eval_count=10, tokens_per_s=100.0
        )


def _load(outcome):
    return json.loads((outcome.run_dir / "report.json").read_text(encoding="utf-8"))


def test_tool_supplies_the_imports_the_model_leaves_out(tmp_path):
    outcome = run_generate(SOURCE, FakeClient(NO_IMPORTS), tmp_path, confirm=lambda _c: True)
    ex = _load(outcome)["execution"]
    assert outcome.exit_code == 0
    assert ex["tests_generated"] >= 7
    assert ex["quarantined"] == 0
    assert ex["passed"] == ex["tests_generated"]
    src = (outcome.run_dir / "candidate_passing.py").read_text(encoding="utf-8")
    assert "from pricing import apply_discount" in src
    assert "import pytest" in src


def test_broken_tests_are_distinguished_from_disagreements(tmp_path):
    outcome = run_generate(SOURCE, FakeClient(MIXED), tmp_path, confirm=lambda _c: True)
    ex = _load(outcome)["execution"]
    quarantined = [t for t in ex["tests"] if t["outcome"] == "quarantined"]
    assert outcome.exit_code == 1
    assert {t["reason"] for t in quarantined} == {"test_failed", "test_error"}
    for t in quarantined:
        if t["reason"] == "test_error":
            assert t["error_type"] == "NameError"
        else:
            assert "NameError" not in t["message"]
    assert ex["quarantine_reasons"]["test_failed"] >= 1
    assert ex["quarantine_reasons"]["test_error"] >= 1