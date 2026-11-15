"""Human-owned acceptance tests for `ltf generate`. Do not edit."""
import ast
import hashlib
import json
import re
import subprocess
import sys
from itertools import cycle
from pathlib import Path

import pytest

from localtestforge.generate import run_generate
from localtestforge.ollama import GenerationResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SOURCE = FIXTURES / "pricing.py"

HEAD = "```python\nimport pytest\nfrom pricing import apply_discount\n\n"
REPLIES = [
    (HEAD + "def test_good():\n    assert apply_discount(100, 10) == 90.0\n```", "stop"),
    (HEAD + "def test_wrong():\n    assert apply_discount(100, 10) == 80.0\n```", "stop"),
    (HEAD + "def test_misuse():\n"
     "    assert apply_discount(100, -1) == pytest.raises(ValueError)\n```", "stop"),
    ("```python\ndef test_bad(:\n    pass\n```", "stop"),
    (HEAD + "def test_cut():\n    assert apply_discount(", "length"),
    ("```python\nimport os\n\ndef test_unsafe():\n    assert os.getcwd()\n```", "stop"),
    (HEAD + "def test_good_raises():\n    with pytest.raises(ValueError):\n"
     "        apply_discount(-1, 10)\n```", "stop"),
]
BAD_SYNTAX = [("```python\ndef test_bad(:\n    pass\n```", "stop")]


class FakeClient:
    model = "fake:1b"
    digest = "sha256:fake"

    def __init__(self, replies=REPLIES):
        self._replies = cycle(replies)
        self.prompts: list[str] = []
        self.options: list[dict] = []

    def generate(self, prompt, options):
        self.prompts.append(prompt)
        self.options.append(dict(options))
        text, reason = next(self._replies)
        return GenerationResult(
            text=text, done_reason=reason, latency_s=0.01, eval_count=10, tokens_per_s=100.0
        )


class DownClient(FakeClient):
    def generate(self, prompt, options):
        raise ConnectionRefusedError("ollama is not running")


def _load(outcome):
    return json.loads((outcome.run_dir / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("gen")
    client = FakeClient()
    before = SOURCE.read_bytes()
    outcome = run_generate(SOURCE, client, out, confirm=lambda _code: True, timeout=30.0)
    return outcome, _load(outcome), client, before


def test_model_is_called_once_per_behavior_with_small_budgets(full_run):
    _, _, client, _ = full_run
    assert len(client.prompts) >= 7
    for prompt, opts in zip(client.prompts, client.options):
        assert "def apply_discount" in prompt
        assert len(prompt) < 4000
        assert opts["num_predict"] <= 300


def test_every_kind_of_model_output_is_classified(full_run):
    outcome, report, _, _ = full_run
    assert outcome.exit_code == 0
    behaviors = report["generation"]["behaviors"]
    assert len(behaviors) >= 7
    statuses = {b["status"] for b in behaviors}
    assert {"accepted", "truncated", "rejected_syntax", "rejected_unsafe"} <= statuses
    assert any(b["generation"]["truncated"] for b in behaviors)


def test_each_test_is_passed_or_quarantined_with_a_reason(full_run):
    _, report, _, _ = full_run
    tests = report["execution"]["tests"]
    assert tests
    for t in tests:
        assert re.match(r"^test_b\d{3}_", t["final_name"]), t["final_name"]
        if "good" in t["final_name"]:
            assert t["outcome"] == "passed"
        elif "wrong" in t["final_name"]:
            assert t["outcome"] == "quarantined"
            assert t["reason"] == "test_failed"
            assert t["message"]
        elif "misuse" in t["final_name"]:
            assert t["outcome"] == "quarantined"
            assert t["reason"] == "lint:LTF005"
        else:
            pytest.fail(f"rejected output leaked into the tests: {t['final_name']}")


def test_candidate_files_keep_raw_output_and_a_clean_passing_subset(full_run):
    outcome, _, _, _ = full_run
    all_src = (outcome.run_dir / "candidate_all.py").read_text(encoding="utf-8")
    ok_src = (outcome.run_dir / "candidate_passing.py").read_text(encoding="utf-8")
    ast.parse(all_src)
    ast.parse(ok_src)
    assert "wrong" in all_src and "misuse" in all_src
    assert "wrong" not in ok_src and "misuse" not in ok_src
    assert "good" in ok_src
    assert "import os" not in all_src


def test_passing_candidate_really_passes_on_the_original(full_run, tmp_path):
    outcome, _, _, _ = full_run
    (tmp_path / "pricing.py").write_bytes(SOURCE.read_bytes())
    (tmp_path / "test_candidate.py").write_bytes(
        (outcome.run_dir / "candidate_passing.py").read_bytes()
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_candidate.py"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout


def test_report_records_everything_needed_to_reproduce(full_run):
    _, report, _, before = full_run
    assert report["model"]["name"] == "fake:1b"
    assert report["model"]["digest"] == "sha256:fake"
    for key in ("num_predict", "temperature", "seed", "repeat_penalty"):
        assert key in report["model"]["options"]
    assert len(report["prompt"]["sha256"]) == 64
    assert report["prompt"]["version"]
    lf = before.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
    assert report["source"]["sha256"] == hashlib.sha256(lf).hexdigest()
    assert report["repair_attempts"] == 0
    assert report["source_unchanged"] is True
    assert SOURCE.read_bytes() == before
    assert "generation" in report and "execution" in report
    for b in report["generation"]["behaviors"]:
        assert {"id", "kind", "description", "status", "generation"} <= set(b)
        assert {"done_reason", "truncated", "latency_s", "eval_count", "tokens_per_s"} <= set(
            b["generation"]
        )


def test_audit_uses_only_passing_tests_and_states_how_many_were_excluded(full_run):
    _, report, _, _ = full_run
    ex = report["execution"]
    assert ex["tests_generated"] == len(ex["tests"])
    assert ex["passed"] + ex["quarantined"] == ex["tests_generated"]
    assert ex["passed"] >= 2 and ex["quarantined"] >= 2
    audit = report["audit"]
    assert audit["tests_used"] == ex["passed"]
    assert audit["tests_excluded"] == ex["quarantined"]
    assert audit["mutation"]["total"] >= 15
    assert audit["coverage"]["line_percent"] > 0
    assert Path(audit["report_path"]).exists()


def test_declining_the_approval_runs_nothing(tmp_path):
    outcome = run_generate(SOURCE, FakeClient(), tmp_path, confirm=lambda _c: False)
    report = _load(outcome)
    assert outcome.exit_code == 0
    assert report["execution"]["status"] == "not_run"
    assert (outcome.run_dir / "candidate_all.py").exists()
    assert not (outcome.run_dir / "candidate_passing.py").exists()
    assert "skipped" in report["audit"]


def test_each_run_gets_a_new_directory(tmp_path):
    dirs = {
        run_generate(SOURCE, FakeClient(), tmp_path, confirm=lambda _c: False).run_dir
        for _ in range(2)
    }
    assert len(dirs) == 2


def test_no_usable_tests_is_reported_not_hidden(tmp_path):
    outcome = run_generate(SOURCE, FakeClient(BAD_SYNTAX), tmp_path, confirm=lambda _c: True)
    report = _load(outcome)
    assert outcome.exit_code == 1
    assert "no passing tests" in report["audit"]["skipped"].lower()


def test_unreachable_model_exits_2_and_records_the_error(tmp_path):
    outcome = run_generate(SOURCE, DownClient(), tmp_path, confirm=lambda _c: True)
    assert outcome.exit_code == 2
    assert _load(outcome)["generation"]["error"]