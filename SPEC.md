# LocalTestForge: Spec

## One-line positioning
An evidence-first auditor for pytest tests (human- or AI-written), plus a generator and
benchmark that measure what small local models can and cannot do.

## Problem
Passing tests are not necessarily good tests. AI-generated tests often assert weakly, or assert
whatever the implementation currently does (the oracle problem). Developers lack a cheap way
to see why a test suite is weak.

## Non-goals
Not an IDE plugin, agent, dashboard, multi-language tool, or Copilot replacement.
Never modifies application code. Never claims generated tests are correct.

## Commands
| Command | Purpose | Needs model? |
|---|---|---|
| `ltf lint <tests>` | Flag weak assertions via AST | No |
| `ltf audit <src> <tests>` | Line/branch coverage + mutation results + explained survivors | No |
| `ltf generate <src> --mode full\|spec` | Generate a candidate test file, then audit it | Yes (Ollama) |
| `ltf bench <dir>` | Run generate+audit over many functions, write a comparison table | Yes |

`--mode full` shows the model the whole function. `--mode spec` shows only signature + docstring.
In spec mode, a test that fails against the implementation is reported as a DISAGREEMENT
(possible bug or stale docs), never repaired.

## Requirements and how each is verified
| ID | Requirement | Verified by |
|---|---|---|
| R1 | `lint` flags exactly the weak tests in `fixtures/tests_weak.py` (is-not-None, no assertion, assert True, isinstance-only) and nothing in `fixtures/tests_strong.py` | tests/acceptance/test_lint.py |
| R2 | `audit` reports line + branch coverage and per-mutant outcome (killed / survived / inconclusive) | tests/acceptance/test_audit.py |
| R3 | Strong fixture tests kill every non-equivalent mutant; weak fixture tests kill strictly fewer | tests/acceptance/test_audit.py |
| R4 | Each survivor is reported with line number, operator, and a before/after diff | acceptance test + manual review |
| R5 | `generate` writes a candidate to a NEW `artifacts/<run_id>/`, never overwrites, never edits source | acceptance test (hash and dir check) |
| R6 | Candidates with syntax errors, forbidden imports/calls, or skip/xfail are NOT executed | unit tests with crafted candidates |
| R7 | Execution requires explicit human approval (or `--approve`) | unit test |
| R8 | Report records model name+digest, prompt version/hash, options+seed, source hash, latency, candidate code, pytest result, coverage, mutation, repair attempts (0) | schema test on report.json |
| R9 | Generation quality and execution quality are reported separately | schema test |
| R10 | Spec-mode failures are labeled DISAGREEMENT and never auto-fixed | acceptance test with a deliberately buggy fixture |
| R11 | `bench` runs on >= 10 fixture functions and writes a markdown table | manual run + file check |
| R12 | Works on native Windows, $0 cost, no network except localhost Ollama | manual run on my machine |

## Mutation operators (v1, own small AST mutator)
Comparison flip (< <-> <=, > <-> >=, == <-> !=), arithmetic swap (+ <-> -, * <-> /),
boolean flip (and <-> or), constant boundary (n -> n+1 / n-1), min <-> max, return-value removal.
Known limitation: equivalent mutants inflate survivors. Document it. Never present a
mutation score as an absolute measure of test quality.

## Milestones
- M1: Baseline experiment script (can a 1.5B model write better-than-trivial tests?).
- M2: Package scaffold + `lint` + mutator + `audit`.
- M3: `generate` (full + spec modes) and the report.
- M4: `bench`, README with limitations, results table, demo recording.

## Execution safety (be honest)
Tests run in a temp directory with a timeout. This is NOT isolation. A malicious test could read
files, environment variables, or the network. Trusted code only. Real isolation is a future item.

## Decisions v1
1. Forbidden imports (root module): os, sys, subprocess, socket, requests, urllib, http, shutil,
   ctypes, importlib, pathlib. Forbidden calls: open, eval, exec, compile, __import__, input.
   Also reject pytest.skip, pytest.xfail, pytest.importorskip, pytest.mark.skip,
   pytest.mark.skipif, pytest.mark.xfail. This is a heuristic guardrail, not security.
2. `ltf audit` ALSO writes artifacts/<run_id>/report.json (new directory every run) and prints a summary.
3. Module name under test = stem of the source file. Each mutant runs in its own temp directory
   containing <stem>.py and the test file, with cwd and PYTHONPATH set to that directory,
   PYTHONDONTWRITEBYTECODE=1, `-p no:cacheprovider`, and a unique COVERAGE_FILE.
4. Outcomes: killed = pytest exit 1 and at least one failed test. survived = exit 0 and at least
   one passed test. inconclusive = timeout, collection error (exit 2), any other exit code,
   zero tests run, all tests skipped, or the mutant failed to compile (and the mutator should
   verify with compile() and drop invalid mutants).
5. Human-owned fixtures: fixtures/ and tests/acceptance/ are provided by the user. Never create or edit them.
6. Default model tag: qwen2.5-coder:1.5b. Prompt templates live as versioned text files in
   src/localtestforge/prompts/ (e.g. full_v1.txt, spec_v1.txt); their name and hash go in report.json.
   Local Ollama endpoint uses http://127.0.0.1:11434.
7. Lint output contract: one plain-text line per finding, printed with typer.echo (no rich tables),
   formatted `path:line: RULE test_name: message`. Exit code 1 if anything is flagged, else 0.
   Rules: LTF001 `is not None` only, LTF002 constant assert, LTF003 isinstance-only,
   LTF004 test with no assertion and no pytest.raises.
8. Adopt Windows mitigations and standards: process-tree kill on timeout via taskkill (shell=False),
   unique COVERAGE_FILE per run, normalize line endings to LF before hashing and diffing.
   `models.py` uses stdlib dataclasses only (no Pydantic).
