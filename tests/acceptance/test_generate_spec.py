"""Human-owned acceptance tests for `ltf generate --mode spec` (R10). Do not edit."""
import json
from itertools import cycle
from pathlib import Path

import pytest

from localtestforge.generate import run_generate
from localtestforge.ollama import GenerationResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
BUGGY = FIXTURES / "buggy_pricing.py"

OK = """\
def test_ok():
    assert apply_discount(100, 10) == 90.0"""
CAP = """\
def test_cap():
    assert apply_discount(100, 80) == 50.0"""
RAISES = """\
def test_raises():
    with pytest.raises(ValueError):
        apply_discount(-1, 10)"""


def _reply(body: str):
    return f"```python\n{body}\n```", "stop"


REPLIES = [_reply(OK), _reply(CAP), _reply(RAISES)]


class FakeClient:
    model = "fake:1b"
    digest = "sha256:fake"

    def __init__(self):
        self._replies = cycle(REPLIES)
        self.prompts: list[str] = []

    def generate(self, prompt, options):
        self.prompts.append(prompt)
        text, reason = next(self._replies)
        return GenerationResult(
            text=text, done_reason=reason, latency_s=0.01, eval_count=10, tokens_per_s=100.0
        )


def _load(outcome):
    return json.loads((outcome.run_dir / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("spec")
    client = FakeClient()
    before = BUGGY.read_bytes()
    outcome = run_generate(BUGGY, client, out, confirm=lambda _c: True, mode="spec")
    return outcome, _load(outcome), client, before


def test_prompts_contain_the_spec_but_never_the_implementation(spec_run):
    _, _, client, _ = spec_run
    assert len(client.prompts) >= 7
    for prompt in client.prompts:
        assert "def apply_discount" in prompt
        assert "percentage discount" in prompt
        assert "effective" not in prompt
        assert "round(price" not in prompt
        assert "raise ValueError" not in prompt
        assert "price must be non-negative" not in prompt


def test_behaviors_come_from_the_docstring_not_the_body(spec_run):
    _, report, _, _ = spec_run
    behaviors = report["generation"]["behaviors"]
    assert {b["kind"] for b in behaviors} >= {"normal", "param", "spec_claim"}
    assert len(behaviors) == 7
    assert not any("price < 0" in b["description"] for b in behaviors)


def test_report_says_it_ran_in_spec_mode(spec_run):
    outcome, report, _, _ = spec_run
    assert outcome.exit_code == 0
    assert report["mode"] == "spec"
    assert report["prompt"]["template"].startswith("spec")


def test_a_contradiction_is_a_disagreement_never_a_repair(spec_run):
    outcome, report, _, _ = spec_run
    tests = report["execution"]["tests"]
    cap = [t for t in tests if "cap" in t["final_name"]]
    assert cap
    for t in cap:
        assert t["outcome"] == "quarantined"
        assert t["reason"] == "test_failed"
        assert "20.0" in t["message"]
    for t in tests:
        if "cap" not in t["final_name"]:
            assert t["outcome"] == "passed"
    assert set(report["execution"]["disagreements"]) == {t["final_name"] for t in cap}
    all_src = (outcome.run_dir / "candidate_all.py").read_text(encoding="utf-8")
    ok_src = (outcome.run_dir / "candidate_passing.py").read_text(encoding="utf-8")
    assert "== 50.0" in all_src
    assert "== 50.0" not in ok_src
    assert report["repair_attempts"] == 0


def test_the_implementation_under_test_is_never_modified(spec_run):
    _, report, _, before = spec_run
    assert BUGGY.read_bytes() == before
    assert report["source_unchanged"] is True