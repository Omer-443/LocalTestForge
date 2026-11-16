"""Generate command implementation and orchestration (SPEC Decisions v1 & v2)."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from localtestforge.audit import (
    compute_lf_sha256,
    create_unique_run_dir,
    get_environment_info,
    run_audit,
)
from localtestforge.behaviors import enumerate_behaviors
from localtestforge.checks import check_candidate
from localtestforge.linter import _find_ltf005, lint_file
from localtestforge.runner import RunResult, run_in_temp_dir

__all__ = [
    "DEFAULT_OPTIONS",
    "GenerateOutcome",
    "build_deterministic_header",
    "classify_quarantine_reason",
    "extract_code",
    "run_generate",
]

DEFAULT_OPTIONS: dict[str, Any] = {
    "num_predict": 200,
    "temperature": 0,
    "seed": 42,
    "repeat_penalty": 1.1,
}


@dataclass(frozen=True)
class GenerateOutcome:
    """Outcome of running test candidate generation."""

    exit_code: int
    run_dir: Path
    report: dict[str, Any]


def extract_code(text: str) -> str:
    """Extract python code from model output, handling fenced or unclosed fences."""
    match = re.search(r"```(?:python)?\s*\n?", text)
    if match:
        rest = text[match.end():]
        close_match = re.search(r"\n?```", rest)
        if close_match:
            return rest[:close_match.start()].strip()
        return rest.strip()
    return text.strip()


def build_deterministic_header(tree: ast.AST, module_stem: str) -> list[str]:
    """Build deterministic header imports for candidate files."""
    header = ["import pytest"]
    public_funcs: list[str] = []
    body = getattr(tree, "body", [])
    for node in body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
            and node.name not in public_funcs
        ):
            public_funcs.append(node.name)
    if public_funcs:
        header.append(f"from {module_stem} import {', '.join(public_funcs)}")
    return header


def classify_quarantine_reason(
    run_res: RunResult,
) -> tuple[str, str | None, str]:
    """Classify a quarantined test run into (reason, error_type, message)."""
    if run_res.timed_out or run_res.status == "timeout":
        return "timeout", None, "test execution timed out"

    if run_res.status in ("collection_error", "harness_error"):
        msg = (run_res.output_tail or "pytest collection/harness error")[:300]
        return "error", None, msg

    failure = run_res.failures[0] if run_res.failures else None

    # Check for collection/infrastructure failure in junit
    if failure and failure.get("tag") == "error":
        msg_raw = failure.get("message", "")
        if "collection failure" in msg_raw.lower() or run_res.counts.get("tests", 0) == 0:
            return "error", None, (msg_raw or run_res.output_tail or "pytest collection error")[:300]

    # If no failure element was parsed from junit
    if not failure:
        if run_res.counts.get("tests", 0) == 0:
            return "error", None, (run_res.output_tail or "pytest collection/infrastructure error")[:300]
        lines = [
            line.strip()
            for line in run_res.output_tail.splitlines()
            if line.strip().startswith("E   ") or "AssertionError" in line
        ]
        msg = (" ".join(lines) if lines else run_res.output_tail)[:300]
        return "test_failed", None, msg

    # Determine the type from the junit failure/error element (type attribute or message)
    type_attr = failure.get("type", "").strip()
    msg_attr = failure.get("message", "").strip()
    text = failure.get("text", "")

    exc_type = ""
    if type_attr:
        exc_type = type_attr.split(".")[-1]
    elif msg_attr:
        colon_idx = msg_attr.find(":")
        if colon_idx != -1:
            first_word = msg_attr[:colon_idx].strip()
            if first_word.isidentifier():
                exc_type = first_word.split(".")[-1]
        elif msg_attr.startswith(("assert ", "assert\n")):
            exc_type = "AssertionError"

    # Fallback to text if exc_type still not found
    if not exc_type and text:
        m_err = re.search(r"E\s+([A-Za-z_][A-Za-z0-9_]*):", text)
        if m_err:
            exc_type = m_err.group(1)
        elif "AssertionError" in text:
            exc_type = "AssertionError"

    if exc_type == "AssertionError" or (not exc_type and "AssertionError" in text):
        reason = "test_failed"
        error_type = None
        lines = [
            line.strip()
            for line in run_res.output_tail.splitlines()
            if line.strip().startswith("E   ") or "AssertionError" in line
        ]
        msg = (" ".join(lines) if lines else (msg_attr or run_res.output_tail))[:300]
    else:
        reason = "test_error"
        error_type = exc_type or "Error"
        lines = [
            line.strip()
            for line in run_res.output_tail.splitlines()
            if line.strip().startswith("E   ")
        ]
        msg = (" ".join(lines) if lines else (msg_attr or run_res.output_tail))[:300]

    return reason, error_type, msg


def _get_prompt_template() -> tuple[str, str, str]:
    """Return (template_name, version, template_text)."""
    template_name = "behavior_v2.txt"
    version = "behavior_v2"
    prompt_path = Path(__file__).resolve().parent / "prompts" / template_name
    template_text = prompt_path.read_text(encoding="utf-8")
    return template_name, version, template_text


def run_generate(
    source: Path,
    client: Any,
    out_dir: Path,
    *,
    confirm: Callable[[str], bool],
    timeout: float = 30.0,
    options: dict[str, Any] | None = None,
) -> GenerateOutcome:
    """Generate candidate tests for source, assemble, filter, evaluate, and audit."""
    if not source.exists() or not source.is_file():
        typer.echo(f"error: {source}: file not found", err=True)
        return GenerateOutcome(exit_code=2, run_dir=out_dir, report={"error": f"{source}: file not found"})

    try:
        source_text = source.read_text(encoding="utf-8")
        tree = ast.parse(source_text, filename=str(source))
    except (SyntaxError, UnicodeDecodeError) as exc:
        typer.echo(f"error: {source}: {exc}", err=True)
        return GenerateOutcome(exit_code=2, run_dir=out_dir, report={"error": str(exc)})

    merged_options = dict(DEFAULT_OPTIONS)
    if options:
        merged_options.update(options)

    started_iso = datetime.now(UTC).isoformat()
    src_hash_before = compute_lf_sha256(source_text)
    env_info = get_environment_info()

    template_name, template_version, template_text = _get_prompt_template()
    prompt_sha256 = hashlib.sha256(template_text.replace("\r\n", "\n").encode("utf-8")).hexdigest()

    run_id, run_dir = create_unique_run_dir(out_dir)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    behaviors = enumerate_behaviors(tree)
    func_nodes: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    behavior_records: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    accepted_test_nodes: list[tuple[str, str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    header_imports = build_deterministic_header(tree, source.stem)
    deduped_imports: list[str] = list(header_imports)

    # a. Prompt -> client.generate per behavior
    for b in behaviors:
        func_node = func_nodes.get(b.function_name)
        func_source = (
            ast.get_source_segment(source_text, func_node)
            if func_node is not None
            else ""
        ) or (ast.unparse(func_node) if func_node is not None else "")

        prompt = template_text.format(
            module_name=source.stem,
            function_source=func_source,
            behavior_description=b.description,
        )

        try:
            gen_res = client.generate(prompt, merged_options)
        except OSError as exc:
            finished_iso = datetime.now(UTC).isoformat()
            src_hash_after = compute_lf_sha256(source.read_text(encoding="utf-8"))
            report = {
                "run_id": run_id,
                "started": started_iso,
                "finished": finished_iso,
                "environment": env_info,
                "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
                "source": {"path": str(source), "sha256": src_hash_before},
                "model": {"name": getattr(client, "model", ""), "digest": getattr(client, "digest", None), "options": merged_options},
                "prompt": {"template": template_name, "version": template_version, "sha256": prompt_sha256},
                "generation": {
                    "behaviors": behavior_records,
                    "counts": status_counts,
                    "error": str(exc),
                },
                "execution": {
                    "status": "not_run",
                    "tests_generated": 0,
                    "passed": 0,
                    "quarantined": 0,
                    "quarantine_reasons": {},
                    "tests": [],
                },
                "audit": {"skipped": f"generation failed: {exc}"},
                "lint_warnings": [],
                "repair_attempts": 0,
                "source_unchanged": (src_hash_before == src_hash_after),
            }
            (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            return GenerateOutcome(exit_code=2, run_dir=run_dir, report=report)

        # Save raw response
        (raw_dir / f"{b.id}.txt").write_text(gen_res.text, encoding="utf-8")

        # b. Truncation check
        if gen_res.done_reason == "length":
            status = "truncated"
        else:
            # c. Extract code and check syntax, safety, and presence of tests
            code = extract_code(gen_res.text)
            try:
                cand_tree = ast.parse(code)
                violations = check_candidate(cand_tree)
                if violations:
                    status = "rejected_unsafe"
                else:
                    test_funcs = [
                        stmt
                        for stmt in cand_tree.body
                        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and stmt.name.startswith("test_")
                    ]
                    if not test_funcs:
                        status = "rejected_no_tests"
                    else:
                        status = "accepted"
                        # Collect imports
                        for stmt in cand_tree.body:
                            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                                imp_str = ast.unparse(stmt)
                                if imp_str not in deduped_imports:
                                    deduped_imports.append(imp_str)
                        # Collect test functions
                        for stmt in test_funcs:
                            orig_name = stmt.name
                            suffix = orig_name[5:]
                            final_name = f"test_{b.id.lower()}_{suffix}"
                            stmt.name = final_name
                            accepted_test_nodes.append((orig_name, final_name, b.id, stmt))
            except SyntaxError:
                status = "rejected_syntax"

        status_counts[status] = status_counts.get(status, 0) + 1
        behavior_records.append(
            {
                "id": b.id,
                "kind": b.kind,
                "description": b.description,
                "status": status,
                "generation": {
                    "done_reason": gen_res.done_reason,
                    "truncated": (gen_res.done_reason == "length"),
                    "latency_s": gen_res.latency_s,
                    "eval_count": gen_res.eval_count,
                    "tokens_per_s": gen_res.tokens_per_s,
                },
            }
        )

    # d. Assembly
    if accepted_test_nodes:
        test_blocks = [ast.unparse(item[3]) for item in accepted_test_nodes]
        candidate_all_source = "\n".join(deduped_imports) + "\n\n\n" + "\n\n\n".join(test_blocks) + "\n"
    else:
        candidate_all_source = "\n".join(deduped_imports) + "\n"

    (run_dir / "candidate_all.py").write_text(candidate_all_source, encoding="utf-8")

    # If no accepted tests at all, audit skipped
    if not accepted_test_nodes:
        finished_iso = datetime.now(UTC).isoformat()
        src_hash_after = compute_lf_sha256(source.read_text(encoding="utf-8"))
        report = {
            "run_id": run_id,
            "started": started_iso,
            "finished": finished_iso,
            "environment": env_info,
            "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
            "source": {"path": str(source), "sha256": src_hash_before},
            "model": {"name": getattr(client, "model", ""), "digest": getattr(client, "digest", None), "options": merged_options},
            "prompt": {"template": template_name, "version": template_version, "sha256": prompt_sha256},
            "generation": {
                "behaviors": behavior_records,
                "counts": status_counts,
            },
            "execution": {
                "status": "completed",
                "tests_generated": 0,
                "passed": 0,
                "quarantined": 0,
                "quarantine_reasons": {},
                "tests": [],
            },
            "audit": {"skipped": "no passing tests"},
            "lint_warnings": [],
            "repair_attempts": 0,
            "source_unchanged": (src_hash_before == src_hash_after),
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return GenerateOutcome(exit_code=1, run_dir=run_dir, report=report)

    # e. Approval
    approved = confirm(candidate_all_source)
    if not approved:
        finished_iso = datetime.now(UTC).isoformat()
        src_hash_after = compute_lf_sha256(source.read_text(encoding="utf-8"))
        report = {
            "run_id": run_id,
            "started": started_iso,
            "finished": finished_iso,
            "environment": env_info,
            "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
            "source": {"path": str(source), "sha256": src_hash_before},
            "model": {"name": getattr(client, "model", ""), "digest": getattr(client, "digest", None), "options": merged_options},
            "prompt": {"template": template_name, "version": template_version, "sha256": prompt_sha256},
            "generation": {
                "behaviors": behavior_records,
                "counts": status_counts,
            },
            "execution": {
                "status": "not_run",
                "reason": "human declined",
                "tests_generated": len(accepted_test_nodes),
                "passed": 0,
                "quarantined": 0,
                "quarantine_reasons": {},
                "tests": [],
            },
            "audit": {"skipped": "human declined"},
            "lint_warnings": [],
            "repair_attempts": 0,
            "source_unchanged": (src_hash_before == src_hash_after),
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return GenerateOutcome(exit_code=0, run_dir=run_dir, report=report)

    # f. Per-test evaluation
    test_eval_records: list[dict[str, Any]] = []
    passing_test_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    passed_count = 0
    quarantined_count = 0
    quarantine_reasons: dict[str, int] = {}

    for orig_name, final_name, b_id, func_stmt in accepted_test_nodes:
        # Check LTF005
        ltf005_findings = _find_ltf005(func_stmt, "", final_name)
        if ltf005_findings:
            typer.echo(f"WARNING: {final_name} flagged with LTF005: quarantined without running", err=True)
            quarantined_count += 1
            reason = "lint:LTF005"
            quarantine_reasons[reason] = quarantine_reasons.get(reason, 0) + 1
            test_eval_records.append(
                {
                    "name": orig_name,
                    "final_name": final_name,
                    "behavior_id": b_id,
                    "outcome": "quarantined",
                    "reason": reason,
                    "message": ltf005_findings[0].message[:300],
                }
            )
            continue

        # Run alone in its own pytest subprocess
        single_test_code = "\n".join(deduped_imports) + "\n\n\n" + ast.unparse(func_stmt) + "\n"
        run_res, _ = run_in_temp_dir(
            source_code=source_text,
            test_code=single_test_code,
            stem=source.stem,
            test_filename="test_single.py",
            with_cov=False,
            timeout=timeout,
        )

        if run_res.status == "passed" and run_res.counts.get("passed", 0) >= 1:
            passed_count += 1
            passing_test_nodes.append(func_stmt)
            test_eval_records.append(
                {
                    "name": orig_name,
                    "final_name": final_name,
                    "behavior_id": b_id,
                    "outcome": "passed",
                    "reason": None,
                    "message": None,
                }
            )
        else:
            quarantined_count += 1
            reason, error_type, msg = classify_quarantine_reason(run_res)
            quarantine_reasons[reason] = quarantine_reasons.get(reason, 0) + 1

            rec: dict[str, Any] = {
                "name": orig_name,
                "final_name": final_name,
                "behavior_id": b_id,
                "outcome": "quarantined",
                "reason": reason,
                "message": msg,
            }
            if reason == "test_error" and error_type:
                rec["error_type"] = error_type

            test_eval_records.append(rec)

    # g. Write candidate_passing.py if any passed
    if not passing_test_nodes:
        finished_iso = datetime.now(UTC).isoformat()
        src_hash_after = compute_lf_sha256(source.read_text(encoding="utf-8"))
        report = {
            "run_id": run_id,
            "started": started_iso,
            "finished": finished_iso,
            "environment": env_info,
            "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
            "source": {"path": str(source), "sha256": src_hash_before},
            "model": {"name": getattr(client, "model", ""), "digest": getattr(client, "digest", None), "options": merged_options},
            "prompt": {"template": template_name, "version": template_version, "sha256": prompt_sha256},
            "generation": {
                "behaviors": behavior_records,
                "counts": status_counts,
            },
            "execution": {
                "status": "completed",
                "tests_generated": len(accepted_test_nodes),
                "passed": 0,
                "quarantined": quarantined_count,
                "quarantine_reasons": quarantine_reasons,
                "tests": test_eval_records,
            },
            "audit": {"skipped": "no passing tests"},
            "lint_warnings": [],
            "repair_attempts": 0,
            "source_unchanged": (src_hash_before == src_hash_after),
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return GenerateOutcome(exit_code=1, run_dir=run_dir, report=report)

    passing_blocks = [ast.unparse(stmt) for stmt in passing_test_nodes]
    candidate_passing_source = "\n".join(deduped_imports) + "\n\n\n" + "\n\n\n".join(passing_blocks) + "\n"
    passing_path = run_dir / "candidate_passing.py"
    passing_path.write_text(candidate_passing_source, encoding="utf-8")

    # h. Audit on candidate_passing.py
    audit_out_dir = run_dir / "audit"
    run_audit(
        src=source,
        tests=passing_path,
        out_dir=audit_out_dir,
        timeout=timeout,
        verbose=False,
        silent=True,
    )
    audit_rep_file = next(audit_out_dir.glob("*/report.json"))
    audit_rep = json.loads(audit_rep_file.read_text(encoding="utf-8"))

    mut_data = audit_rep.get("mutation", {})
    survivors = [
        m for m in mut_data.get("mutants", [])
        if m.get("outcome") == "survived"
    ]
    mutation_summary = {
        "total": mut_data.get("total", 0),
        "killed": mut_data.get("killed", 0),
        "survived": mut_data.get("survived", 0),
        "inconclusive": mut_data.get("inconclusive", 0),
        "survivors": survivors,
    }

    audit_summary = {
        "tests_used": passed_count,
        "tests_excluded": quarantined_count,
        "coverage": audit_rep.get("coverage", {}),
        "mutation": mutation_summary,
        "report_path": str(audit_rep_file.resolve()),
    }

    # i. Lint on passing tests (LTF001-004)
    lint_findings = lint_file(passing_path)
    lint_warnings = [
        {"rule": f.rule, "line": f.line, "test": f.test_name, "message": f.message}
        for f in lint_findings
        if f.rule in {"LTF001", "LTF002", "LTF003", "LTF004"}
    ]

    finished_iso = datetime.now(UTC).isoformat()
    src_hash_after = compute_lf_sha256(source.read_text(encoding="utf-8"))

    report = {
        "run_id": run_id,
        "started": started_iso,
        "finished": finished_iso,
        "environment": env_info,
        "execution_policy": "temp workspace + timeout; NOT isolation; trusted code only",
        "source": {"path": str(source), "sha256": src_hash_before},
        "model": {"name": getattr(client, "model", ""), "digest": getattr(client, "digest", None), "options": merged_options},
        "prompt": {"template": template_name, "version": template_version, "sha256": prompt_sha256},
        "generation": {
            "behaviors": behavior_records,
            "counts": status_counts,
        },
        "execution": {
            "status": "completed",
            "tests_generated": len(accepted_test_nodes),
            "passed": passed_count,
            "quarantined": quarantined_count,
            "quarantine_reasons": quarantine_reasons,
            "tests": test_eval_records,
        },
        "audit": audit_summary,
        "lint_warnings": lint_warnings,
        "repair_attempts": 0,
        "source_unchanged": (src_hash_before == src_hash_after),
    }

    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return GenerateOutcome(exit_code=0, run_dir=run_dir, report=report)
