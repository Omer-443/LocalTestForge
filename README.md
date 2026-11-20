# LocalTestForge

**An evidence-first auditor for Python tests, plus a local-model test generator that treats
everything the model writes as untrusted.**

A passing test is not necessarily a good test. LocalTestForge measures what a test suite
actually protects, and when it uses a small local model (via Ollama) to write tests, it runs
every generated test on its own, quarantines the ones that fail, and reports coverage and
mutation results only for the tests that survived.

> Status: v0.3, working prototype. Python and pytest only. Developed and tested on Windows.

## What it does

| Command | Purpose |
|---|---|
| `ltf lint <tests.py>` | Flags weak or broken assertions using the AST. No model needed. |
| `ltf audit <src.py> <tests.py>` | Line and branch coverage, plus mutation testing with each surviving mutant explained. |
| `ltf generate <src.py>` | Per-behavior test generation with a local model, per-test quarantine, then an audit of the passing tests. |
| `ltf generate <src.py> --mode spec` | Same, but the model sees only the signature and docstring, never the body. |

### Lint rules

| Rule | Flags |
|---|---|
| LTF001 | assertion only checks `is not None` |
| LTF002 | assertion on a constant (`assert True`) |
| LTF003 | assertion only checks `isinstance` |
| LTF004 | test with no assertion and no `pytest.raises` |
| LTF005 | comparison against a `pytest.raises(...)` call (a broken test) |

LTF005 exists because a small model produced this pattern in 2 of 4 early samples.

## Example

```
$ ltf audit fixtures/pricing.py fixtures/tests_weak.py
Original run: passed
Coverage: 81.8% lines, 66.7% branches
Mutants killed: 9/25 (36%), 16 survived
line 15: if price < 0:
  M005 comparison_flip: price < 0 -> price <= 0
    likely gap: no test sits exactly on this boundary
...
```

[screenshot: audit output on the weak vs strong suite]

## Quickstart (Windows PowerShell)

```powershell
git clone <repo-url>; cd LocalTestForge
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ltf lint fixtures\tests_weak.py
ltf audit fixtures\pricing.py fixtures\tests_strong.py

# optional: generation needs Ollama (https://ollama.com)
ollama pull qwen2.5-coder:1.5b
ltf generate fixtures\pricing.py
```

Every run writes a new `artifacts/<run_id>/` folder containing `report.json` (model name and
digest, prompt version and hash, generation options, source hash, per-behavior latency and
token counts, every test's outcome) and the generated test files. Nothing is overwritten.

## How `generate` works

1. Enumerate behaviors from the AST: the normal case, each boolean parameter value, each
   `raise`, each branch (full mode), or each docstring bullet (spec mode).
2. Ask the model for **one short test per behavior** (small token budget, so a weak model
   cannot ramble or loop).
3. Reject truncated, syntactically invalid, or unsafe output (forbidden imports and calls,
   skip/xfail, top-level code).
4. Assemble a file with a deterministic import header (boilerplate only; test bodies are
   never edited).
5. Show you the code and ask for approval before anything runs.
6. Run each test alone. A test that fails against the original code is **quarantined** with
   its evidence, never repaired. Tests flagged LTF005 are quarantined without running.
7. Compute coverage and mutation results **only from the passing tests** and say how many
   were excluded.

There is no auto-repair: the tool never weakens an assertion to make a test pass.

## Findings

Single deterministic runs on one function (`pricing.py`, 25 mutants), temperature 0. Coverage
and mutation are computed on the tests that actually passed.

| Suite | Usable tests | Line / branch cov. | Mutants killed |
|---|---|---|---|
| Qwen2.5-Coder 1.5B, whole-file generation | 2 of 5 | 63.6% / 50.0% | 2/25 (8%) |
| Qwen2.5-Coder 1.5B, per-behavior | 4 of 7 | 90.9% / 83.3% | 9/25 (36%) |
| Qwen2.5-Coder 3B, per-behavior | 4 of 6 | 90.9% / 83.3% | 10/25 (40%) |
| Hand-written weak suite | 6 | 81.8% / 66.7% | 9/25 (36%) |
| Hand-written strong suite | 12 | 100% / 100% | 25/25 (100%) |

[add rows after the constant-import fix, and latency and memory per model]

Observations (n=1 per row, so treat them as observations, not benchmarks):

- **Coverage misled.** The 1.5B whole-file run reported 100% line and branch coverage while 3
  of its 5 tests were wrong. Computed from the 2 passing tests it was 63.6% and 50.0%.
- **The model is good at structure and weak at expected values.** Exception tests were
  reliable. Arithmetic expectations were frequently wrong, for example expecting 60.0 where
  the code gives 70.0, which is the oracle problem in practice.
- **Per-behavior prompts helped a small model.** Short, narrow tasks avoided the repetition loop
  that whole-file prompting produced in one run.
- **Model size helped modestly.** The 3B model produced fewer wrong expectations, but both
  models plateaued near the hand-written weak suite.
- **Spec mode did not find the planted bug.** On `buggy_pricing.py` (documented cap missing),
  the 3B model chose the right input (75% discount) but wrote a wrong expected value (150.0;
  the documented answer is 100.0). A disagreement is a prompt to look, not proof of a bug.

## Design decisions

- **Quarantine over repair.** Letting a model fix its own failing assertions would hide
  exactly the errors this tool exists to expose.
- **Separate generation quality from execution quality.** A truncated reply, a syntax error,
  a broken test (`NameError`) and a disagreement (`AssertionError`) are different failures and
  are reported separately.
- **Human-owned acceptance tests.** The tool was built with an AI coding agent, and the agent
  was not allowed to edit the fixtures and acceptance tests that judge its work. The model
  client is injectable, so the generation pipeline is tested with a scripted fake model;
  real-model failures found later became regression tests.
- **Reproducibility.** Reports record model digest, prompt hash, options and source hash.
  At temperature 0 the seed has no effect, so identical inputs give identical output.

## Limitations

- **No isolation.** Tests run in a temporary directory with a timeout. This is not a sandbox.
  Use only on code you trust. The generation step asks for approval before running anything.
- **Mutation scores are an indicator, not a measure of test quality.** The built-in mutator
  has six operators, and equivalent mutants (changes that cannot alter behavior) are counted
  as survivors.
- Python and pytest only, one module at a time. Evidence here covers one function and two
  small models; it is not a benchmark.
- The audit runs one pytest process per mutant (about 1.2 s each on the test machine), so large
  modules will be slow.
- Tests that reference module constants depend on the constant itself, so mutants changing that
  constant can survive.

## Related work

Tools and research such as Qodo Cover (Cover-Agent), Mutahunter, Meta's TestGen-LLM and MutGen
use coverage or mutation feedback to steer LLM test generation. LocalTestForge borrows the idea
and differs in emphasis: it assumes a small local model is unreliable, quarantines per test,
audits only what survived, offers a spec-only mode, and reports every failure type separately.

## Development

Strict TDD against human-owned acceptance tests: `pytest tests/unit` and
`pytest tests/acceptance` (the acceptance suite takes a few minutes because it runs real
mutation audits). Lint and types: `ruff check src tests`, `mypy src`.

## Next

Reference-implementation check for disagreements, parallel mutant execution, real isolation,
a benchmark across more functions and models.