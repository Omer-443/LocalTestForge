# LocalTestForge: Agent Instructions

LocalTestForge (`ltf`) is a Python CLI that audits test quality (weak-assertion lint,
coverage, mutation testing with explained survivors) and generates pytest candidates
with a local Ollama model, with evidence attached. Read `SPEC.md` before any task.

## Environment
- A virtual environment already exists at `venv` with pytest, pytest-cov, typer, rich, ruff, mypy.
- Use it. NEVER create another environment. NEVER install packages without asking.
- Platform is native Windows (PowerShell). No bash-only commands.

## Working style
- Work in small steps: ONE module or feature per task. Show a plan first; wait for my approval.
- After implementing, run the tests and report results honestly. Stop. Do not continue to the next feature.
- If something in SPEC.md is ambiguous, ask. Do not guess.

## Hard rules (never violate)
1. NEVER weaken, delete, skip, xfail, or rewrite a test to make it pass. If a test fails, explain why and stop.
2. NEVER edit anything under `fixtures/` or `tests/acceptance/`. Those are human-owned ground truth.
3. NEVER modify a target source file under audit. Mutants run on temp copies only.
4. NEVER overwrite previous run output. Each run writes to a new `artifacts/<run_id>/` directory.
5. NEVER auto-repair a model-generated assertion failure. Report it.
6. NEVER describe temp-dir execution as a "sandbox". Say: "trusted code only, no isolation".
7. NEVER make network calls except to `http://localhost:11434` (Ollama).
8. Do not add dependencies without asking. Allowed: typer, rich, pytest, pytest-cov, ruff, mypy.
9. If a human-owned test or fixture cannot pass because of paths, layout or environment,
   STOP and report the cause. NEVER copy, duplicate, symlink or recreate fixtures to work around it.
10. Report EVERY file you changed, including config files, in your final summary. Never edit
    config (pyproject.toml, rules, workflows) unless the task says so; ask first.
       
## Code standards
- Python 3.11+, type hints on all function signatures, docstrings on public functions.
- Use `pathlib`, `subprocess.run([...], shell=False)`.
- Prefer the standard library (`ast`, `json`, `hashlib`, `tempfile`, `urllib`).
- Small pure functions. Keep I/O at the edges so logic is unit-testable.
- Command output must be understandable to a developer who has never read the code.