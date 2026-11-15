"""AST-based mutation testing engine for LocalTestForge."""

from __future__ import annotations

import ast
import copy
import difflib
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["Mutant", "generate_mutants"]

CMP_FLIP_MAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}

ARITH_SWAP_MAP: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}

BOOL_FLIP_MAP: dict[type[ast.boolop], type[ast.boolop]] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


@dataclass(frozen=True)
class Mutant:
    """Represents a single mutation applied to a source module."""

    id: str
    line: int
    operator: str
    original: str
    mutated: str
    mutated_source: str

    @property
    def diff(self) -> str:
        """Return a short unified diff of original vs mutated snippet, LF-normalized."""
        orig_lines = [line + "\n" for line in self.original.splitlines()]
        mut_lines = [line + "\n" for line in self.mutated.splitlines()]
        diff_lines = list(
            difflib.unified_diff(
                orig_lines,
                mut_lines,
                fromfile="original",
                tofile="mutated",
                lineterm="\n",
            )
        )
        return "".join(diff_lines).replace("\r\n", "\n")


@dataclass
class _MutationPlan:
    """Specification for applying a mutation to a target AST node."""

    node_index: int
    line: int
    col_offset: int
    operator: str
    original_snippet: str
    mutated_snippet: str
    apply_fn: Callable[[ast.AST], None]


def _collect_ignored_node_ids(tree: ast.AST, nodes: list[ast.AST]) -> tuple[set[int], set[int]]:
    """Collect node ids for docstrings and type annotations to prevent invalid mutations."""
    docstring_ids: set[int] = set()
    annotation_ids: set[int] = set()

    # Module-level docstring
    if isinstance(tree, ast.Module) and tree.body:
        first_stmt = tree.body[0]
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(first_stmt.value, ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            docstring_ids.add(id(first_stmt))
            docstring_ids.add(id(first_stmt.value))

    for node in nodes:
        # Class and function docstrings
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
        ):
            first_stmt = node.body[0]
            if (
                isinstance(first_stmt, ast.Expr)
                and isinstance(first_stmt.value, ast.Constant)
                and isinstance(first_stmt.value.value, str)
            ):
                docstring_ids.add(id(first_stmt))
                docstring_ids.add(id(first_stmt.value))

        # Function return and parameter annotations
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                for sub in ast.walk(node.returns):
                    annotation_ids.add(id(sub))
            args_to_check = (
                node.args.posonlyargs
                + node.args.args
                + node.args.kwonlyargs
            )
            for arg in args_to_check:
                if arg.annotation is not None:
                    for sub in ast.walk(arg.annotation):
                        annotation_ids.add(id(sub))
            if node.args.vararg and node.args.vararg.annotation is not None:
                for sub in ast.walk(node.args.vararg.annotation):
                    annotation_ids.add(id(sub))
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                for sub in ast.walk(node.args.kwarg.annotation):
                    annotation_ids.add(id(sub))

        # Variable annotations (e.g. x: int = 5)
        elif isinstance(node, ast.AnnAssign):
            for sub in ast.walk(node.annotation):
                annotation_ids.add(id(sub))

        # TypeAlias (Python 3.12+)
        elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            for sub in ast.walk(node.value):
                annotation_ids.add(id(sub))

    return docstring_ids, annotation_ids


def generate_mutants(source: str) -> list[Mutant]:
    """Generate all valid, non-equivalent mutants from Python source code.

    Args:
        source: Full Python module source code.

    Returns:
        List of Mutant instances, sorted deterministically by (line, column, operator).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    orig_unparsed = ast.unparse(tree)
    orig_nodes = list(ast.walk(tree))
    docstring_ids, annotation_ids = _collect_ignored_node_ids(tree, orig_nodes)

    plans: list[_MutationPlan] = []

    for idx, node in enumerate(orig_nodes):
        if not hasattr(node, "lineno"):
            continue

        line: int = node.lineno
        col: int = getattr(node, "col_offset", 0)

        # 1. comparison_flip
        if isinstance(node, ast.Compare):
            for op_idx, cmp_op in enumerate(node.ops):
                cmp_type = type(cmp_op)
                if cmp_type in CMP_FLIP_MAP:
                    target_cmp_cls = CMP_FLIP_MAP[cmp_type]
                    orig_cmp_snippet = ast.unparse(node)
                    dummy_cmp = copy.deepcopy(node)
                    dummy_cmp.ops[op_idx] = target_cmp_cls()
                    mut_cmp_snippet = ast.unparse(dummy_cmp)

                    def make_apply_cmp(oi: int, cls: type[ast.cmpop]) -> Callable[[ast.AST], None]:
                        def apply(target: ast.AST) -> None:
                            assert isinstance(target, ast.Compare)
                            target.ops[oi] = cls()
                        return apply

                    plans.append(
                        _MutationPlan(
                            node_index=idx,
                            line=line,
                            col_offset=col,
                            operator="comparison_flip",
                            original_snippet=orig_cmp_snippet,
                            mutated_snippet=mut_cmp_snippet,
                            apply_fn=make_apply_cmp(op_idx, target_cmp_cls),
                        )
                    )

        # 2. arithmetic_swap (BinOp and AugAssign)
        elif isinstance(node, (ast.BinOp, ast.AugAssign)):
            op_type = type(node.op)
            if op_type in ARITH_SWAP_MAP:
                target_arith_cls = ARITH_SWAP_MAP[op_type]
                orig_arith_snippet = ast.unparse(node)
                dummy_arith = copy.deepcopy(node)
                dummy_arith.op = target_arith_cls()
                mut_arith_snippet = ast.unparse(dummy_arith)

                def make_apply_arith(cls: type[ast.operator]) -> Callable[[ast.AST], None]:
                    def apply(target: ast.AST) -> None:
                        assert isinstance(target, (ast.BinOp, ast.AugAssign))
                        target.op = cls()
                    return apply

                plans.append(
                    _MutationPlan(
                        node_index=idx,
                        line=line,
                        col_offset=col,
                        operator="arithmetic_swap",
                        original_snippet=orig_arith_snippet,
                        mutated_snippet=mut_arith_snippet,
                        apply_fn=make_apply_arith(target_arith_cls),
                    )
                )

        # 3. boolean_flip (BoolOp)
        elif isinstance(node, ast.BoolOp):
            bool_type = type(node.op)
            if bool_type in BOOL_FLIP_MAP:
                target_bool_cls = BOOL_FLIP_MAP[bool_type]
                orig_bool_snippet = ast.unparse(node)
                dummy_bool = copy.deepcopy(node)
                dummy_bool.op = target_bool_cls()
                mut_bool_snippet = ast.unparse(dummy_bool)

                def make_apply_bool(cls: type[ast.boolop]) -> Callable[[ast.AST], None]:
                    def apply(target: ast.AST) -> None:
                        assert isinstance(target, ast.BoolOp)
                        target.op = cls()
                    return apply

                plans.append(
                    _MutationPlan(
                        node_index=idx,
                        line=line,
                        col_offset=col,
                        operator="boolean_flip",
                        original_snippet=orig_bool_snippet,
                        mutated_snippet=mut_bool_snippet,
                        apply_fn=make_apply_bool(target_bool_cls),
                    )
                )

        # 4. constant_boundary (numeric constants n -> n+1 and n-1)
        elif (
            isinstance(node, ast.Constant)
            and id(node) not in docstring_ids
            and id(node) not in annotation_ids
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            val = node.value
            for new_val in (val + 1, val - 1):
                orig_const_snippet = ast.unparse(node)
                dummy_const = copy.deepcopy(node)
                dummy_const.value = new_val
                mut_const_snippet = ast.unparse(dummy_const)

                def make_apply_const(v: float) -> Callable[[ast.AST], None]:
                    def apply(target: ast.AST) -> None:
                        assert isinstance(target, ast.Constant)
                        target.value = v
                    return apply

                plans.append(
                    _MutationPlan(
                        node_index=idx,
                        line=line,
                        col_offset=col,
                        operator="constant_boundary",
                        original_snippet=orig_const_snippet,
                        mutated_snippet=mut_const_snippet,
                        apply_fn=make_apply_const(new_val),
                    )
                )

        # 5. min_max_swap (calls to min/max swapped)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("min", "max")
        ):
            new_func_name = "max" if node.func.id == "min" else "min"
            orig_call_snippet = ast.unparse(node)
            dummy_call = copy.deepcopy(node)
            assert isinstance(dummy_call.func, ast.Name)
            dummy_call.func.id = new_func_name
            mut_call_snippet = ast.unparse(dummy_call)

            def make_apply_func(name: str) -> Callable[[ast.AST], None]:
                def apply(target: ast.AST) -> None:
                    assert isinstance(target, ast.Call)
                    assert isinstance(target.func, ast.Name)
                    target.func.id = name
                return apply

            plans.append(
                _MutationPlan(
                    node_index=idx,
                    line=line,
                    col_offset=col,
                    operator="min_max_swap",
                    original_snippet=orig_call_snippet,
                    mutated_snippet=mut_call_snippet,
                    apply_fn=make_apply_func(new_func_name),
                )
            )

        # 6. return_removal (return <expr> -> return None)
        elif (
            isinstance(node, ast.Return)
            and node.value is not None
            and not (isinstance(node.value, ast.Constant) and node.value.value is None)
        ):
            orig_ret_snippet = ast.unparse(node)
            dummy_ret = copy.deepcopy(node)
            dummy_ret.value = ast.Constant(value=None)
            mut_ret_snippet = ast.unparse(dummy_ret)

            def apply_return(target: ast.AST) -> None:
                assert isinstance(target, ast.Return)
                target.value = ast.Constant(value=None)

            plans.append(
                _MutationPlan(
                    node_index=idx,
                    line=line,
                    col_offset=col,
                    operator="return_removal",
                    original_snippet=orig_ret_snippet,
                    mutated_snippet=mut_ret_snippet,
                    apply_fn=apply_return,
                )
            )

    # Apply each mutation to a deepcopy of the original tree
    valid_candidates: list[tuple[int, int, str, str, str, str]] = []

    for plan in plans:
        tree_copy = copy.deepcopy(tree)
        copy_nodes = list(ast.walk(tree_copy))
        target_node = copy_nodes[plan.node_index]
        plan.apply_fn(target_node)
        mutated_source = ast.unparse(tree_copy)

        # Verification: drop if compile fails
        is_compilable = True
        try:
            compile(mutated_source, "<mutant>", "exec")
        except (SyntaxError, ValueError):
            is_compilable = False

        if not is_compilable:
            continue

        # Drop no-ops
        if mutated_source == orig_unparsed or plan.original_snippet == plan.mutated_snippet:
            continue

        valid_candidates.append(
            (
                plan.line,
                plan.col_offset,
                plan.operator,
                plan.original_snippet,
                plan.mutated_snippet,
                mutated_source,
            )
        )

    # Deterministic ordering by (line, column, operator, mutated_snippet)
    valid_candidates.sort(key=lambda item: (item[0], item[1], item[2], item[4]))

    # Construct Mutant objects with stable IDs M001, M002, ...
    result: list[Mutant] = []
    for i, (line, _col, operator, orig_snip, mut_snip, mut_src) in enumerate(
        valid_candidates, start=1
    ):
        result.append(
            Mutant(
                id=f"M{i:03d}",
                line=line,
                operator=operator,
                original=orig_snip,
                mutated=mut_snip,
                mutated_source=mut_src,
            )
        )

    return result
