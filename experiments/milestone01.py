#!/usr/bin/env python3
"""LocalTestForge - Milestone 01

Question under test:
    Can a local Qwen2.5-Coder-1.5B generate better-than-trivial pytest tests
    for ONE simple Python function on this machine?

Policy (see contract):
  * Source file is never modified (hash checked before/after).
  * Generated tests are saved as a CANDIDATE in a new run directory; nothing
    existing is ever overwritten.
  * No auto-repair. Zero repair attempts are recorded.
  * Execution = temp workspace + timeout. This is NOT a sandbox. Trusted,
    synthetic code only. Human approval is required before running tests.
  * Generation quality and execution quality are reported separately.

Dependencies: Python 3.9+, pytest, pytest-cov, a running Ollama server.
No third-party imports in this file.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

PROMPT_VERSION = "m01-v2"
DEFAULT_MODEL = "qwen2.5-coder:1.5b"
OLLAMA_URL = "http://localhost:11434"
GEN_OPTIONS = {"temperature": 0, "seed": 42, "num_predict": 1024}
GEN_TIMEOUT_S = 300
PYTEST_TIMEOUT_S = 60

# Manual mutants for target/pricing.py: (name, old, new). `old` must occur once.
MANUAL_MUTANTS = [
    ("price_boundary", "if price < 0:", "if price <= 0:"),
    ("percent_upper_boundary", "0 <= percent <= 100", "0 <= percent < 100"),
    ("cap_min_to_max", "min(percent, MAX_DISCOUNT)", "max(percent, MAX_DISCOUNT)"),
    ("member_bonus_sign", "effective += MEMBER_BONUS", "effective -= MEMBER_BONUS"),
]

# Heuristic static checks on generated tests. These are guardrails against
# obvious mistakes, NOT a security boundary.
FORBIDDEN_IMPORT_ROOTS = {
    "os", "sys", "subprocess", "socket", "requests", "urllib", "http",
    "shutil", "ctypes", "importlib", "pathlib",
}
FORBIDDEN_CALLS = {"open", "eval", "exec", "compile", "__import__", "input"}
INTEGRITY_PATTERNS = {
    "pytest.skip", "pytest.xfail", "pytest.importorskip",
    "pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail",
}


# ----------------------------------------------------------------- helpers
def sha256(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


# ------------------------------------------------------ source analysis (AST)
def analyze_source(source: str) -> list[dict]:
    """Describe top-level public functions: signature, docstring, raises, branches."""
    tree = ast.parse(source)
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            raises = sorted({
                ast.unparse(n.exc.func) if isinstance(n.exc, ast.Call) else ast.unparse(n.exc)
                for n in ast.walk(node)
                if isinstance(n, ast.Raise) and n.exc is not None
            })
            out.append({
                "name": node.name,
                "signature": f"({ast.unparse(node.args)})",
                "returns": ast.unparse(node.returns) if node.returns else None,
                "has_docstring": ast.get_docstring(node) is not None,
                "raises": raises,
                "branch_points": sum(isinstance(n, (ast.If, ast.IfExp)) for n in ast.walk(node)),
            })
    return out


def build_prompt(module: str, source: str) -> str:
    return (
        f"You are writing pytest tests for a Python module named `{module}`.\n\n"
        f"Source code:\n```python\n{source}\n```\n\n"
        "Requirements:\n"
        f"- Import the code with `from {module} import ...`.\n"
        "- Write pytest tests only. Output exactly one Python code block and nothing else.\n"
        "- Cover normal cases, boundary values, and every exception the code raises "
        "(use pytest.raises).\n"
        "- Every test must contain at least one assertion with a concrete expected value "
        "that you compute by hand from the code.\n"
        "- Write one behavior per test function: one assertion per test, except pytest.raises tests.\n"
        "- Write at least one test for every parameter, including boolean flags, so every branch runs.\n"
        "- Do not use skip/xfail, file access, network, subprocess, or randomness.\n"
        "- Do not modify or reimplement the code under test.\n"
    )


# ------------------------------------------------------------ Ollama client
def ollama_request(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    url = OLLAMA_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def model_digest(model: str) -> str | None:
    try:
        for m in ollama_request("/api/tags").get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return m.get("digest")
    except Exception:
        pass
    return None


def generate(model: str, prompt: str) -> dict:
    t0 = time.perf_counter()
    resp = ollama_request(
        "/api/generate",
        {"model": model, "prompt": prompt, "stream": False, "options": GEN_OPTIONS},
        timeout=GEN_TIMEOUT_S,
    )
    latency = time.perf_counter() - t0
    eval_count = resp.get("eval_count")
    eval_ns = resp.get("eval_duration")
    return {
        "text": resp.get("response", ""),
        "latency_s": round(latency, 2),
        "eval_count": eval_count,
        "tokens_per_s": round(eval_count / (eval_ns / 1e9), 2) if eval_count and eval_ns else None,
    }


def extract_code(response: str) -> tuple[str, bool]:
    """Return (code, had_fence)."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip() + "\n", True
    return response.strip() + "\n", False


# ---------------------------------------------- generation-quality analysis
def analyze_generated(code: str, module: str, target_funcs: list[str]) -> dict:
    res = {
        "syntax_valid": False, "syntax_error": None,
        "imports_target_module": False,
        "target_functions_called": [],
        "test_function_count": 0,
        "assertion_count": 0,
        "trivial_assertion_count": 0,
        "tests_without_assertions": [],
        "blocking_flags": [],
    }
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        res["syntax_error"] = f"{e.msg} (line {e.lineno})"
        return res
    res["syntax_valid"] = True

    called, flags = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == module:
                res["imports_target_module"] = True
            if node.module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                flags.append(f"forbidden import: {node.module}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == module:
                    res["imports_target_module"] = True
                if a.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    flags.append(f"forbidden import: {a.name}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    flags.append(f"forbidden call: {node.func.id}()")
                if node.func.id in target_funcs:
                    called.add(node.func.id)
        if isinstance(node, (ast.Attribute, ast.Name)):
            text = ast.unparse(node)
            if text in INTEGRITY_PATTERNS:
                flags.append(f"skip/xfail usage: {text}")
    res["target_functions_called"] = sorted(called)
    res["blocking_flags"] = sorted(set(flags))

    tests = [n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    res["test_function_count"] = len(tests)
    for t in tests:
        n_assert = 0
        for n in ast.walk(t):
            if isinstance(n, ast.Assert):
                n_assert += 1
                if isinstance(n.test, ast.Constant) and n.test.value:
                    res["trivial_assertion_count"] += 1
            elif isinstance(n, ast.With):
                if any("pytest.raises" in ast.unparse(i.context_expr) for i in n.items):
                    n_assert += 1
        res["assertion_count"] += n_assert
        if n_assert == 0:
            res["tests_without_assertions"].append(t.name)
    return res


# -------------------------------------------------------- execution + results
def parse_junit(path: Path) -> dict:
    if not path.exists():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in tot:
            tot[k] += int(s.attrib.get(k, 0))
    return tot


def classify(returncode: int, counts: dict) -> str:
    if returncode == 0:
        return "passed"
    if returncode == 1:
        if counts["tests"] == 0:
            return "harness_error"  # pytest never ran (e.g. not installed): not a test result
        return "test_failures"      # assertion failure or exception inside a test
    if returncode == 2:
        return "collection_or_interrupt_error"
    if returncode == 5:
        return "no_tests_collected"
    return f"pytest_exit_{returncode}"


def run_pytest(workdir: Path, test_file: str, module: str, with_cov: bool) -> dict:
    junit = workdir / "junit.xml"
    cmd = [sys.executable, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider",
           f"--rootdir={workdir}", f"--junitxml={junit}"]
    if with_cov:
        cmd += [f"--cov={module}", "--cov-branch",
                f"--cov-report=json:{workdir / 'coverage.json'}"]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=PYTEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "duration_s": PYTEST_TIMEOUT_S, "counts": None,
                "output_tail": ""}
    counts = parse_junit(junit)
    return {
        "status": classify(proc.returncode, counts),
        "returncode": proc.returncode,
        "duration_s": round(time.perf_counter() - t0, 2),
        "counts": counts,
        "output_tail": "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:]),
    }


def parse_coverage(workdir: Path, module_file: str) -> dict | None:
    p = workdir / "coverage.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    entry = next((v for k, v in data.get("files", {}).items()
                  if k.replace("\\", "/").endswith(module_file)), None)
    if entry is None:
        return None
    s = entry["summary"]
    nb = s.get("num_branches", 0)
    return {
        "line_percent": round(100 * s["covered_lines"] / s["num_statements"], 1)
        if s["num_statements"] else 100.0,
        "branch_percent": round(100 * s.get("covered_branches", 0) / nb, 1) if nb else None,
        "num_statements": s["num_statements"],
        "missing_lines": entry.get("missing_lines", []),
        "num_branches": nb,
        "covered_branches": s.get("covered_branches", 0),
        "missing_branches": entry.get("missing_branches", []),
    }


def evaluate_in_workspace(source_text: str, module: str, candidate_code: str,
                          with_cov: bool) -> tuple[dict, dict | None]:
    with tempfile.TemporaryDirectory(prefix="ltf_") as tmp:
        wd = Path(tmp)
        (wd / f"{module}.py").write_text(source_text, encoding="utf-8")
        (wd / "test_candidate.py").write_text(candidate_code, encoding="utf-8")
        result = run_pytest(wd, "test_candidate.py", module, with_cov)
        cov = parse_coverage(wd, f"{module}.py") if with_cov else None
        return result, cov


def run_mutants(source_text: str, module: str, candidate_code: str) -> list[dict]:
    results = []
    for name, old, new in MANUAL_MUTANTS:
        if source_text.count(old) != 1:
            results.append({"mutant": name, "outcome": "invalid_mutant",
                            "detail": f"pattern found {source_text.count(old)} times"})
            continue
        res, _ = evaluate_in_workspace(source_text.replace(old, new), module,
                                       candidate_code, with_cov=False)
        outcome = {"test_failures": "killed", "passed": "survived"}.get(
            res["status"], f"inconclusive:{res['status']}")
        results.append({"mutant": name, "change": f"{old}  ->  {new}", "outcome": outcome})
    return results


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="LocalTestForge Milestone 01")
    ap.add_argument("--source", default="target/pricing.py")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default="artifacts")
    ap.add_argument("--approve", action="store_true",
                    help="skip the interactive approval prompt (own synthetic code only)")
    ap.add_argument("--candidate", help="evaluate this existing test file instead of "
                    "calling the model (harness self-check)")
    args = ap.parse_args()

    source_path = Path(args.source).resolve()
    source_text = source_path.read_text(encoding="utf-8")
    src_hash = sha256(source_path.read_bytes())
    module = source_path.stem
    funcs = analyze_source(source_text)
    func_names = [f["name"] for f in funcs]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)   # never overwrite a previous run

    prompt = build_prompt(module, source_text)
    report: dict = {
        "run_id": run_id, "started": now_iso(),
        "milestone": "01", "prompt_version": PROMPT_VERSION,
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "pytest": pkg_version("pytest"), "pytest-cov": pkg_version("pytest-cov"),
        },
        "execution_policy": "temp workspace + timeout; NOT a sandbox; trusted synthetic code only",
        "source": {"path": str(source_path), "sha256": src_hash, "functions": funcs},
        "model": {"name": args.model, "digest": None, "options": GEN_OPTIONS,
                  "skipped_using_candidate": bool(args.candidate)},
        "prompt_sha256": sha256(prompt),
        "repair_attempts": 0,
        "generation": {}, "execution": None, "coverage": None, "mutation": None,
    }
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    def finish(code: int) -> int:
        report["source_unchanged"] = sha256(source_path.read_bytes()) == src_hash
        report["finished"] = now_iso()
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nReport: {run_dir / 'report.json'}")
        return code

    # ---- Stage 1: obtain candidate -------------------------------------
    if args.candidate:
        code = Path(args.candidate).read_text(encoding="utf-8")
        report["generation"]["origin"] = f"external file: {args.candidate}"
    else:
        report["model"]["digest"] = model_digest(args.model)
        print(f"Generating with {args.model} ...")
        try:
            g = generate(args.model, prompt)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            report["generation"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            print(f"Generation failed: {e}\nIs Ollama running and the model pulled?")
            return finish(2)
        (run_dir / "raw_response.txt").write_text(g["text"], encoding="utf-8")
        code, had_fence = extract_code(g["text"])
        report["generation"].update({
            "ok": True, "latency_s": g["latency_s"], "eval_count": g["eval_count"],
            "tokens_per_s": g["tokens_per_s"], "response_had_code_fence": had_fence,
        })

    cand_path = run_dir / f"test_{module}_candidate.py"
    cand_path.write_text(code, encoding="utf-8")
    report["generation"]["candidate_path"] = str(cand_path)
    report["generation"]["candidate_sha256"] = sha256(code)
    report["generation"]["analysis"] = analyze_generated(code, module, func_names)
    a = report["generation"]["analysis"]

    print("\n" + "=" * 70 + "\nGENERATED TEST CANDIDATE\n" + "=" * 70)
    print(code)
    print("=" * 70)
    print(f"syntax_valid={a['syntax_valid']}  tests={a['test_function_count']}  "
          f"assertions={a['assertion_count']}  imports_target={a['imports_target_module']}")

    # ---- Gate: generation problems stop here (no auto-repair) -----------
    if not a["syntax_valid"]:
        print(f"Stopped: generated code is not valid Python ({a['syntax_error']}).")
        report["execution"] = {"status": "not_run", "reason": "generation: invalid syntax"}
        return finish(1)
    if a["blocking_flags"]:
        print("Stopped: static checks flagged the candidate:")
        for f in a["blocking_flags"]:
            print("  -", f)
        report["execution"] = {"status": "not_run", "reason": "generation: blocking flags"}
        return finish(1)

    # ---- Human approval ------------------------------------------------
    if not args.approve:
        if input("\nExecute this candidate? (trusted code only) [y/N]: ").strip().lower() != "y":
            report["execution"] = {"status": "not_run", "reason": "human declined"}
            return finish(0)

    # ---- Stage 2: execution + coverage ---------------------------------
    print("Running pytest with branch coverage ...")
    res, cov = evaluate_in_workspace(source_text, module, code, with_cov=True)
    report["execution"], report["coverage"] = res, cov
    print(f"pytest: {res['status']}  {res['counts']}")
    if cov:
        print(f"coverage: lines {cov['line_percent']}%  branches {cov['branch_percent']}%  "
              f"missing branches {cov['missing_branches']}")

    # ---- Stage 3: mutants (only meaningful if tests pass on the original)
    if res["status"] == "passed":
        print("Running manual mutants ...")
        report["mutation"] = run_mutants(source_text, module, code)
        killed = sum(m["outcome"] == "killed" for m in report["mutation"])
        print(f"mutants killed: {killed}/{len(report['mutation'])}")
        for m in report["mutation"]:
            print(f"  {m['mutant']:<24} {m['outcome']}")
    else:
        print("Mutation analysis skipped: candidate does not pass on the original source.")
        print("(No auto-repair in M1. Review the candidate and the output below.)")
        print(res.get("output_tail", ""))
        report["mutation"] = {"skipped": "candidate did not pass on original source"}

    return finish(0)


if __name__ == "__main__":
    sys.exit(main())
