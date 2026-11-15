"""Pure candidate code checks.

Heuristic guardrail, not security.
Disallows forbidden imports, forbidden calls, skip/xfail patterns, and top-level code
other than imports and test_* functions.
"""

from __future__ import annotations

import ast

__all__ = [
    "FORBIDDEN_CALLS",
    "FORBIDDEN_IMPORT_ROOTS",
    "check_candidate",
    "is_candidate_safe",
]

FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "ctypes",
        "http",
        "importlib",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "socket",
        "subprocess",
        "sys",
        "urllib",
    }
)

FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "__import__",
        "compile",
        "eval",
        "exec",
        "input",
        "open",
    }
)

FORBIDDEN_PYTEST_CALLS: frozenset[str] = frozenset(
    {
        "importorskip",
        "skip",
        "xfail",
    }
)

FORBIDDEN_DECORATOR_ATTRS: frozenset[str] = frozenset(
    {
        "skip",
        "skipif",
        "xfail",
    }
)


def check_candidate(code_or_tree: str | ast.AST) -> list[str]:
    """Check candidate test code against safety rules.

    Returns a list of violation descriptions (empty if safe).
    Heuristic guardrail, not security.
    """
    tree: ast.AST
    if isinstance(code_or_tree, str):
        try:
            tree = ast.parse(code_or_tree)
        except SyntaxError as exc:
            return [f"Syntax error: {exc}"]
    else:
        tree = code_or_tree

    violations: list[str] = []

    # 1. Top-level statements check: only imports and test_* functions allowed.
    body = getattr(tree, "body", [])
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not stmt.name.startswith("test_"):
                violations.append(
                    f"Forbidden top-level non-test function '{stmt.name}'. "
                    "Only test_* functions and imports are permitted."
                )
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            # Module docstring
            continue
        violations.append(
            f"Forbidden top-level statement '{type(stmt).__name__}'. "
            "Only test_* functions and imports are permitted."
        )

    # 2. Walk AST to detect forbidden imports, calls, and skip/xfail decorators
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"Forbidden import '{alias.name}' (root '{root}')")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"Forbidden import from '{node.module}' (root '{root}')")
        elif isinstance(node, ast.Call):
            func = node.func
            # Check forbidden built-in calls
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                violations.append(f"Forbidden call '{func.id}'")
            elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
                violations.append(f"Forbidden call '{func.attr}'")

            # Check pytest skip / xfail / importorskip calls
            if isinstance(func, ast.Attribute):
                if func.attr in FORBIDDEN_PYTEST_CALLS and (
                    (isinstance(func.value, ast.Name) and func.value.id == "pytest")
                    or (isinstance(func.value, ast.Attribute) and func.value.attr == "mark")
                ):
                    violations.append(f"Forbidden pytest call '{ast.unparse(func)}'")
            elif isinstance(func, ast.Name) and func.id in FORBIDDEN_PYTEST_CALLS:
                violations.append(f"Forbidden call '{func.id}'")

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                dec_target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    (isinstance(dec_target, ast.Attribute) and dec_target.attr in FORBIDDEN_DECORATOR_ATTRS)
                    or (isinstance(dec_target, ast.Name) and dec_target.id in FORBIDDEN_DECORATOR_ATTRS)
                ):
                    violations.append(f"Forbidden decorator '{ast.unparse(decorator)}'")

    return violations


def is_candidate_safe(code_or_tree: str | ast.AST) -> bool:
    """Return True if candidate passes all checks.

    Heuristic guardrail, not security.
    """
    return len(check_candidate(code_or_tree)) == 0
