"""Weak-assertion linter for pytest test files.

Pure AST analysis — no imports of the file under analysis, no subprocess calls.
All I/O is handled by the caller (cli.py); this module only returns Finding objects.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A single weak-assertion finding."""

    path: str
    line: int
    rule: str
    test_name: str
    message: str

    def __str__(self) -> str:
        """Format as ``path:line: RULE test_name: message``."""
        return f"{self.path}:{self.line}: {self.rule} {self.test_name}: {self.message}"


# ---------------------------------------------------------------------------
# Rule messages
# ---------------------------------------------------------------------------

_MESSAGES: dict[str, str] = {
    "LTF001": "assertion only checks `is not None` or `!= None` — value is not tested",
    "LTF002": "assertion on a constant — always passes regardless of the code under test",
    "LTF003": "assertion only checks type via isinstance() — value is not tested",
    "LTF004": "test has no assertion and no pytest.raises — it can never fail",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_pytest_raises(node: ast.stmt) -> bool:
    """Return True if *node* is ``with pytest.raises(...)``."""
    if not isinstance(node, ast.With):
        return False
    for item in node.items:
        ctx = item.context_expr
        if isinstance(ctx, ast.Call):
            func = ctx.func
            # pytest.raises(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "raises"
                and isinstance(func.value, ast.Name)
                and func.value.id == "pytest"
            ):
                return True
    return False


def _is_weak_001(test_expr: ast.expr) -> bool:
    """LTF001: sole comparison is ``is not None`` or ``!= None``."""
    # assert x is not None  → Compare(ops=[IsNot()], comparators=[Constant(None)])
    # assert x != None      → Compare(ops=[NotEq()], comparators=[Constant(None)])
    if not isinstance(test_expr, ast.Compare):
        return False
    if len(test_expr.ops) != 1 or len(test_expr.comparators) != 1:
        return False
    op = test_expr.ops[0]
    comp = test_expr.comparators[0]
    if not isinstance(comp, ast.Constant) or comp.value is not None:
        return False
    return isinstance(op, (ast.IsNot, ast.NotEq))


def _is_weak_002(test_expr: ast.expr) -> bool:
    """LTF002: assertion on a bare constant (True, False, 0, 1, …)."""
    return isinstance(test_expr, ast.Constant)


def _is_weak_003(test_expr: ast.expr) -> bool:
    """LTF003: bare ``isinstance(...)`` call as the entire assertion."""
    if not isinstance(test_expr, ast.Call):
        return False
    func = test_expr.func
    return isinstance(func, ast.Name) and func.id == "isinstance"


def _classify_assert(node: ast.Assert) -> str | None:
    """Return the LTF rule code if the assert is weak, else None (strong)."""
    expr = node.test
    if _is_weak_001(expr):
        return "LTF001"
    if _is_weak_002(expr):
        return "LTF002"
    if _is_weak_003(expr):
        return "LTF003"
    return None  # strong


# ---------------------------------------------------------------------------
# Test-body analysis
# ---------------------------------------------------------------------------


def _analyse_body(
    body: list[ast.stmt],
) -> tuple[bool, str | None, int | None]:
    """Analyse a test function body.

    Returns ``(has_strong, first_weak_rule, first_weak_line)``.

    * *has_strong* — True if any strong assertion or pytest.raises was found.
    * *first_weak_rule* / *first_weak_line* — rule code and source line of the
      first weak assert encountered (or ``None`` if none).

    The search is **flat**: we do not recurse into nested functions or classes,
    but we do walk into ``if``/``for``/``try``/``with`` blocks (except the
    ``with pytest.raises`` block itself, which is counted as strong).
    """
    has_strong = False
    first_weak_rule: str | None = None
    first_weak_line: int | None = None

    def _walk(stmts: list[ast.stmt]) -> None:
        nonlocal has_strong, first_weak_rule, first_weak_line

        for stmt in stmts:
            # pytest.raises context manager → strong
            if _is_pytest_raises(stmt):
                has_strong = True
                continue

            if isinstance(stmt, ast.Assert):
                rule = _classify_assert(stmt)
                if rule is None:
                    has_strong = True
                else:
                    if first_weak_rule is None:
                        first_weak_rule = rule
                        first_weak_line = stmt.lineno
                continue

            # Recurse into compound statements (but not into nested
            # function/class definitions — those are separate test units).
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue

            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.stmt):
                    _walk([child])

    _walk(body)
    return has_strong, first_weak_rule, first_weak_line


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _is_test_function(node: ast.stmt) -> bool:
    """True for module-level ``def test_*`` functions."""
    return (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def _test_methods_from_class(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return ``test_*`` methods from a ``Test*`` class."""
    if not node.name.startswith("Test"):
        return []
    methods: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith(
            "test_"
        ):
            methods.append(item)
    return methods


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lint_file(path: Path) -> list[Finding]:
    """Lint a single .py file and return a (possibly empty) list of Findings.

    Raises:
        SyntaxError: if the file cannot be parsed.
        OSError: if the file cannot be read.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))  # raises SyntaxError on bad input

    findings: list[Finding] = []
    path_str = str(path)

    # Collect test nodes: module-level functions + methods inside Test* classes
    test_nodes: list[tuple[str, int, list[ast.stmt]]] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.stmt):
            continue
        if _is_test_function(node):
            assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            test_nodes.append((node.name, node.lineno, node.body))
        elif isinstance(node, ast.ClassDef):
            for method in _test_methods_from_class(node):
                test_nodes.append((method.name, method.lineno, method.body))

    for test_name, def_line, body in test_nodes:
        has_strong, first_weak_rule, first_weak_line = _analyse_body(body)

        if has_strong:
            # At least one strong assertion → test is fine
            continue

        if first_weak_rule is None:
            # No assertions at all and no pytest.raises
            findings.append(
                Finding(
                    path=path_str,
                    line=def_line,
                    rule="LTF004",
                    test_name=test_name,
                    message=_MESSAGES["LTF004"],
                )
            )
        else:
            # All assertions are weak
            assert first_weak_line is not None
            findings.append(
                Finding(
                    path=path_str,
                    line=first_weak_line,
                    rule=first_weak_rule,
                    test_name=test_name,
                    message=_MESSAGES[first_weak_rule],
                )
            )

    return findings
