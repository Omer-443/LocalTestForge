"""AST-based behavior enumeration for target source code."""

from __future__ import annotations

import ast
from dataclasses import dataclass

__all__ = ["Behavior", "enumerate_behaviors", "enumerate_spec_behaviors", "render_function_spec"]


@dataclass(frozen=True)
class Behavior:
    """A distinct behavior of a target function to be tested."""

    id: str
    kind: str
    description: str
    function_name: str


def _is_bool_param(arg: ast.arg, default: ast.expr | None) -> bool:
    """Return True if an argument is annotated as bool or defaults to True/False."""
    if arg.annotation is not None and ast.unparse(arg.annotation) == "bool":
        return True
    return bool(default is not None and isinstance(default, ast.Constant) and isinstance(default.value, bool))


def _collect_bool_param_descriptions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Return list of descriptions for boolean parameters (True then False for each)."""
    descs: list[str] = []
    all_pos = node.args.posonlyargs + node.args.args
    n_pos = len(all_pos)
    n_defaults = len(node.args.defaults)
    for i, arg in enumerate(all_pos):
        default = (
            node.args.defaults[i - (n_pos - n_defaults)]
            if i >= n_pos - n_defaults
            else None
        )
        if _is_bool_param(arg, default):
            descs.append(f"parameter {arg.arg} is True")
            descs.append(f"parameter {arg.arg} is False")

    for i, arg in enumerate(node.args.kwonlyargs):
        kw_default = node.args.kw_defaults[i]
        if _is_bool_param(arg, kw_default):
            descs.append(f"parameter {arg.arg} is True")
            descs.append(f"parameter {arg.arg} is False")

    return descs


def _collect_raises_and_branches(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[int, int, str, str]]:
    """Collect (lineno, sort_key, kind, description) for raises and branches."""
    items: list[tuple[int, int, str, str]] = []

    # 1. Collect all If statements whose body contains no raise
    for node in ast.walk(func_node):
        if isinstance(node, ast.If):
            has_raise = any(
                isinstance(n, ast.Raise)
                for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            )
            if not has_raise:
                test_src = ast.unparse(node.test)
                items.append((node.lineno, 1, "branch_true", f"branch true when {test_src}"))
                items.append((node.lineno, 2, "branch_false", f"branch false when {test_src}"))

    # 2. Collect all Raise statements with their enclosing if condition
    def visit_stmts(
        stmts: list[ast.stmt],
        enclosing_cond: str | None = None,
    ) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Raise):
                if stmt.exc is None:
                    exc_name = "Exception"
                elif isinstance(stmt.exc, ast.Call):
                    exc_name = ast.unparse(stmt.exc.func)
                else:
                    exc_name = ast.unparse(stmt.exc)
                cond_str = enclosing_cond if enclosing_cond else "unconditional"
                items.append((stmt.lineno, 0, "raises", f"raises {exc_name} when {cond_str}"))
            elif isinstance(stmt, ast.If):
                test_src = ast.unparse(stmt.test)
                visit_stmts(stmt.body, enclosing_cond=test_src)
                # If orelse contains an If, it will be handled as an elif statement.
                # If orelse is a regular else block, condition is negated enclosing test.
                if stmt.orelse:
                    if len(stmt.orelse) == 1 and isinstance(stmt.orelse[0], ast.If):
                        visit_stmts(stmt.orelse, enclosing_cond=enclosing_cond)
                    else:
                        visit_stmts(stmt.orelse, enclosing_cond=f"not ({test_src})")
            else:
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.stmt):
                        visit_stmts([child], enclosing_cond=enclosing_cond)

    visit_stmts(func_node.body)

    items.sort(key=lambda x: (x[0], x[1]))
    return items


def enumerate_behaviors(source: str | ast.Module) -> list[Behavior]:
    """Enumerate behaviors across all public top-level functions in source."""
    tree = ast.parse(source) if isinstance(source, str) else source
    behaviors: list[Behavior] = []
    behavior_idx = 1

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue

        func_name = node.name

        # 1. Normal behavior
        b_id = f"B{behavior_idx:03d}"
        behaviors.append(
            Behavior(
                id=b_id,
                kind="normal",
                description=f"normal execution of {func_name}",
                function_name=func_name,
            )
        )
        behavior_idx += 1

        # 2. Param behaviors for boolean-like parameters by parameter position
        for desc in _collect_bool_param_descriptions(node):
            b_id = f"B{behavior_idx:03d}"
            behaviors.append(
                Behavior(
                    id=b_id,
                    kind="param",
                    description=desc,
                    function_name=func_name,
                )
            )
            behavior_idx += 1

        # 3. Raises and branches by source line
        raise_and_branches = _collect_raises_and_branches(node)
        for _, _, kind, desc in raise_and_branches:
            b_id = f"B{behavior_idx:03d}"
            behaviors.append(
                Behavior(
                    id=b_id,
                    kind=kind,
                    description=desc,
                    function_name=func_name,
                )
            )
            behavior_idx += 1

    return behaviors


def enumerate_spec_behaviors(
    source: str | ast.Module,
    warnings: list[str] | None = None,
) -> list[Behavior]:
    """Enumerate behaviors across public functions using signature and docstrings only.

    The implementation body is never inspected.
    Per public function:
      - 1 "normal" behavior
      - 1 "param" behavior per boolean-like parameter and value (True / False)
      - 1 "spec_claim" behavior per docstring bullet line (lines starting with "- " or "* "),
        with description "docstring claim: <text>"
    If a function has no docstring, enumerate only normal and param, and add a warning.
    """
    tree = ast.parse(source) if isinstance(source, str) else source
    behaviors: list[Behavior] = []
    behavior_idx = 1

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue

        func_name = node.name

        # 1. Normal behavior
        b_id = f"B{behavior_idx:03d}"
        behaviors.append(
            Behavior(
                id=b_id,
                kind="normal",
                description=f"normal execution of {func_name}",
                function_name=func_name,
            )
        )
        behavior_idx += 1

        # 2. Param behaviors for boolean-like parameters
        for desc in _collect_bool_param_descriptions(node):
            b_id = f"B{behavior_idx:03d}"
            behaviors.append(
                Behavior(
                    id=b_id,
                    kind="param",
                    description=desc,
                    function_name=func_name,
                )
            )
            behavior_idx += 1

        # 3. Spec claims from docstring bullet lines
        docstring = ast.get_docstring(node, clean=False)
        if docstring is None or not docstring.strip():
            if warnings is not None:
                warnings.append(f"function '{func_name}' has no docstring")
        else:
            for line in docstring.splitlines():
                stripped = line.strip()
                if stripped.startswith(("- ", "* ")):
                    claim_text = stripped[2:].strip()
                    b_id = f"B{behavior_idx:03d}"
                    behaviors.append(
                        Behavior(
                            id=b_id,
                            kind="spec_claim",
                            description=f"docstring claim: {claim_text}",
                            function_name=func_name,
                        )
                    )
                    behavior_idx += 1

    return behaviors


def render_function_spec(func_node: ast.FunctionDef | ast.AsyncFunctionDef | str) -> str:
    """Render a function's specification only (signature plus docstring).

    The function body is replaced with just the docstring (or pass if absent),
    ensuring that the implementation body cannot leak.
    """
    import copy

    if isinstance(func_node, str):
        parsed = ast.parse(func_node)
        for stmt in parsed.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_node = stmt
                break
        else:
            raise ValueError("No function found in source string")

    spec_node = copy.deepcopy(func_node)
    docstring_stmt: ast.stmt | None = None
    if (
        spec_node.body
        and isinstance(spec_node.body[0], ast.Expr)
        and isinstance(spec_node.body[0].value, ast.Constant)
        and isinstance(spec_node.body[0].value.value, str)
    ):
        docstring_stmt = spec_node.body[0]

    if docstring_stmt is not None:
        spec_node.body = [docstring_stmt]
    else:
        spec_node.body = [ast.Pass()]

    return ast.unparse(spec_node)
