"""Mechanical checks for declared calculation-derivation artifacts."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _safe_declared_file(task_dir: Path, declared: object) -> tuple[Path | None, str | None]:
    if not isinstance(declared, str) or not declared:
        return None, "declared artifact path is missing"
    relative = Path(declared)
    if relative.is_absolute():
        return None, "declared artifact path is absolute"
    root = task_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        return None, "declared artifact path escapes the task directory"
    if not path.exists() or not path.is_file():
        return None, f"declared artifact {declared!r} does not exist"
    if path.stat().st_size <= 0:
        return None, f"declared artifact {declared!r} is empty"
    return path, None


def _wolfram_executable_text(source: str) -> str:
    """Remove nested comments and strings before looking for executable calls."""

    output: list[str] = []
    index = 0
    comment_depth = 0
    in_string = False
    while index < len(source):
        pair = source[index : index + 2]
        char = source[index]
        if comment_depth:
            if pair == "(*":
                comment_depth += 1
                index += 2
                continue
            if pair == "*)":
                comment_depth -= 1
                index += 2
                continue
            index += 1
            continue
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if pair == "(*":
            comment_depth = 1
            index += 2
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _wolfram_live_executable_text(source: str) -> str:
    """Remove comments, strings, and syntactically dead ``If[False, ...]`` blocks."""

    executable = _wolfram_executable_text(source)
    while True:
        match = re.search(r"\bIf\s*\[\s*False\s*,", executable)
        if match is None:
            return executable
        bracket_start = executable.find("[", match.start())
        depth = 0
        end = None
        for index in range(bracket_start, len(executable)):
            if executable[index] == "[":
                depth += 1
            elif executable[index] == "]":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    while end < len(executable) and executable[end].isspace():
                        end += 1
                    if end < len(executable) and executable[end] == ";":
                        end += 1
                    break
        if end is None:
            return executable
        executable = executable[: match.start()] + " " + executable[end:]


class _TopLevelRebindingVisitor(ast.NodeVisitor):
    """Find bindings that can replace a selected module-level callable."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.rebound = False

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == self.target and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.rebound = True

    def _visit_definition_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        if node.name == self.target:
            self.rebound = True
        # Defaults and decorators execute in module scope; the function body
        # does not execute during import and has its own local bindings.
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *(default for default in node.args.kw_defaults if default is not None),
        ):
            self.visit(expression)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition_header(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == self.target:
            self.rebound = True
        for expression in (*node.decorator_list, *node.bases):
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambda parameters/body have their own local binding scope.
        return

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if (alias.asname or alias.name.split(".", 1)[0]) == self.target:
                self.rebound = True

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*" or (alias.asname or alias.name) == self.target:
                self.rebound = True

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name == self.target:
            self.rebound = True
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name == self.target:
            self.rebound = True
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name == self.target:
            self.rebound = True


def python_function_interface_errors(
    tree: ast.Module,
    function_name: object,
    declared_parameters: list[object],
) -> list[str]:
    """Validate one import-time-stable, keyword-callable Python interface."""

    if not isinstance(function_name, str) or not function_name:
        return ["result-meta python_function must be a nonempty string"]
    if not all(isinstance(name, str) for name in declared_parameters):
        return ["result-meta parameters must contain only string canonical names"]

    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(functions) != 1:
        return [
            "result Python backend must define python_function "
            f"{function_name!r} exactly once"
        ]
    function = functions[0]
    errors: list[str] = []
    if isinstance(function, ast.AsyncFunctionDef):
        errors.append("result Python backend python_function must be synchronous")
    if function.decorator_list:
        errors.append("result Python backend python_function must not use decorators")

    arguments = function.args
    if arguments.posonlyargs:
        errors.append(
            "result Python backend python_function must accept canonical inputs by keyword"
        )
    if arguments.vararg is not None:
        errors.append("result Python backend python_function must not accept *args")
    if arguments.kwarg is not None:
        errors.append("result Python backend python_function must not accept **kwargs")
    explicit_parameters = [
        argument.arg
        for argument in (
            *arguments.args,
            *arguments.kwonlyargs,
        )
    ]
    if explicit_parameters != declared_parameters:
        errors.append(
            "result-meta parameters must exactly match python_function's explicit "
            f"parameter order: metadata={declared_parameters}, "
            f"function={explicit_parameters}"
        )

    definition_index = tree.body.index(function)
    rebinding = _TopLevelRebindingVisitor(function_name)
    for statement in tree.body[definition_index + 1 :]:
        rebinding.visit(statement)
    if rebinding.rebound:
        errors.append(
            "result Python backend python_function is rebound after its selected definition"
        )
    return errors


def _python_result_depends_on_inputs(tree: ast.Module, function_name: object) -> bool:
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return False

    input_names = {
        argument.arg
        for argument in [
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        ]
    }
    dependencies = {name: True for name in input_names}
    if function.args.vararg is not None:
        dependencies[function.args.vararg.arg] = True
    if function.args.kwarg is not None:
        dependencies[function.args.kwarg.arg] = True
    if not dependencies:
        return False

    def expression_depends(node: ast.AST | None, state: dict[str, bool]) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name):
            return isinstance(node.ctx, ast.Load) and state.get(node.id, False)
        if isinstance(node, ast.NamedExpr):
            value_depends = expression_depends(node.value, state)
            assign_target(node.target, value_depends, state)
            return value_depends
        return any(expression_depends(child, state) for child in ast.iter_child_nodes(node))

    def target_names(target: ast.AST) -> set[str]:
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            return {
                name
                for element in target.elts
                for name in target_names(element)
            }
        return set()

    def assign_target(target: ast.AST, value_depends: bool, state: dict[str, bool]) -> None:
        for name in target_names(target):
            # Assignment is a reaching-definition kill: a later constant write
            # must erase an earlier dependency rather than leave it sticky.
            state[name] = value_depends

    def assign_value(target: ast.AST, value: ast.AST, state: dict[str, bool]) -> None:
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for child_target, child_value in zip(target.elts, value.elts, strict=True):
                assign_value(child_target, child_value, state)
            return
        assign_target(target, expression_depends(value, state), state)

    def join_states(states: list[dict[str, bool]]) -> dict[str, bool]:
        if not states:
            return {}
        names = set().union(*(state.keys() for state in states))
        # A value is accepted as dependent only if every reaching definition is
        # dependent.  This is deliberately a must-dependence join.
        return {
            name: all(state.get(name, False) for state in states)
            for name in names
        }

    def process_block(
        statements: list[ast.stmt],
        incoming: dict[str, bool],
    ) -> tuple[dict[str, bool], bool, list[bool], bool]:
        state = dict(incoming)
        falls_through = True
        return_dependencies: list[bool] = []
        supported = True

        for statement in statements:
            if not falls_through:
                break
            if isinstance(statement, ast.Return):
                return_dependencies.append(expression_depends(statement.value, state))
                falls_through = False
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    assign_value(target, statement.value, state)
            elif isinstance(statement, ast.AnnAssign):
                assign_target(
                    statement.target,
                    expression_depends(statement.value, state),
                    state,
                )
            elif isinstance(statement, ast.AugAssign):
                old_depends = any(state.get(name, False) for name in target_names(statement.target))
                assign_target(
                    statement.target,
                    old_depends or expression_depends(statement.value, state),
                    state,
                )
            elif isinstance(statement, ast.If):
                if isinstance(statement.test, ast.Constant) and statement.test.value is False:
                    state, falls_through, nested_returns, nested_supported = process_block(
                        statement.orelse, state
                    )
                elif isinstance(statement.test, ast.Constant) and statement.test.value is True:
                    state, falls_through, nested_returns, nested_supported = process_block(
                        statement.body, state
                    )
                else:
                    body_state, body_falls, body_returns, body_supported = process_block(
                        statement.body, state
                    )
                    else_state, else_falls, else_returns, else_supported = process_block(
                        statement.orelse, state
                    )
                    reaching = [
                        branch_state
                        for branch_state, branch_falls in (
                            (body_state, body_falls),
                            (else_state, else_falls),
                        )
                        if branch_falls
                    ]
                    state = join_states(reaching)
                    falls_through = bool(reaching)
                    nested_returns = [*body_returns, *else_returns]
                    nested_supported = body_supported and else_supported
                return_dependencies.extend(nested_returns)
                supported = supported and nested_supported
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                loop_state = dict(state)
                if isinstance(statement, (ast.For, ast.AsyncFor)):
                    assign_target(
                        statement.target,
                        expression_depends(statement.iter, loop_state),
                        loop_state,
                    )
                body_state, body_falls, body_returns, body_supported = process_block(
                    statement.body, loop_state
                )
                reaching = [state]
                if body_falls:
                    reaching.append(body_state)
                state = join_states(reaching)
                state, falls_through, else_returns, else_supported = process_block(
                    statement.orelse, state
                )
                return_dependencies.extend([*body_returns, *else_returns])
                supported = supported and body_supported and else_supported
            elif isinstance(statement, ast.With):
                for item in statement.items:
                    if item.optional_vars is not None:
                        assign_target(
                            item.optional_vars,
                            expression_depends(item.context_expr, state),
                            state,
                        )
                state, falls_through, nested_returns, nested_supported = process_block(
                    statement.body, state
                )
                return_dependencies.extend(nested_returns)
                supported = supported and nested_supported
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                state[statement.name] = False
            elif isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    state[alias.asname or alias.name.split(".", 1)[0]] = False
            elif isinstance(statement, ast.Delete):
                for target in statement.targets:
                    for name in target_names(target):
                        state[name] = False
            elif isinstance(statement, (ast.Expr, ast.Assert, ast.Pass)):
                expression_depends(
                    statement.value if isinstance(statement, ast.Expr) else None,
                    state,
                )
            elif isinstance(statement, (ast.Raise, ast.Break, ast.Continue)):
                falls_through = False
            else:
                # Unsupported control flow cannot be positive derivation
                # evidence.  Fail closed instead of reverting to ast.walk's
                # flow-insensitive false-pass behavior.
                supported = False

        return state, falls_through, return_dependencies, supported

    _, _, returns, supported = process_block(function.body, dependencies)
    return supported and bool(returns) and all(returns)


def _wolfram_result_depends_on_package_x(
    source: str,
    *,
    methods: list[object],
    result_symbol: object,
) -> bool:
    if not isinstance(result_symbol, str) or not result_symbol:
        return False
    executable = _wolfram_live_executable_text(source)
    assignments = [
        (match.group(1), match.group(2))
        for match in re.finditer(
            r"\b([A-Za-z$][A-Za-z0-9_$]*)\s*=\s*(.*?)\s*;",
            executable,
            flags=re.DOTALL,
        )
    ]
    derived: set[str] = set()
    for target, expression in assignments:
        depends_on_method = any(
            re.search(rf"\b{re.escape(str(method))}\s*\[", expression)
            for method in methods
        )
        depends_on_derived = any(
            re.search(rf"\b{re.escape(symbol)}\b", expression)
            for symbol in derived
        )
        if depends_on_method or depends_on_derived:
            derived.add(target)
        else:
            # Mathematica Set has reaching-definition/kill semantics too.  A
            # later imported-formula or constant assignment supersedes the
            # earlier Package-X-derived value.
            derived.discard(target)
    return result_symbol in derived


def derivation_artifact_errors(
    task_dir: Path,
    task_id: str,
    task: dict[str, Any] | None,
    result_meta: dict[str, Any],
) -> list[str]:
    """Check that metadata is bound to non-placeholder executable artifacts."""

    errors: list[str] = []
    provenance = result_meta.get("calculation_provenance")
    task_type = task.get("type") if isinstance(task, dict) else None
    if result_meta.get("task_id") != task_id:
        errors.append(
            f"result-meta task_id {result_meta.get('task_id')!r} does not match {task_id!r}"
        )
    if not isinstance(task, dict):
        errors.append(f"task {task_id!r} is absent from calc-tasks.json")
    if result_meta.get("translation_status") != "complete":
        errors.append("translation_status must be 'complete' for verified derivation")
    if provenance == "package_x_derived" and result_meta.get(
        "benchmark_used_as_input"
    ) is not False:
        errors.append(
            "package_x_derived result must have benchmark_used_as_input == false"
        )
    if provenance == "manual_tree_algebra" and task_type == "loop":
        errors.append("loop task cannot use manual_tree_algebra provenance")

    source_wl, source_error = _safe_declared_file(task_dir, result_meta.get("source_wl"))
    if source_error:
        errors.append(source_error)
    python_path, python_error = _safe_declared_file(
        task_dir, result_meta.get("python_file")
    )
    if python_error:
        errors.append(python_error)
    elif python_path is not None:
        try:
            tree = ast.parse(
                python_path.read_text(encoding="utf-8"),
                filename=python_path.as_posix(),
            )
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"declared Python artifact is not parseable: {exc}")
        else:
            function_name = result_meta.get("python_function")
            functions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if function_name not in functions:
                errors.append(
                    f"declared python_function {function_name!r} is not defined"
                )
            elif not _python_result_depends_on_inputs(tree, function_name):
                errors.append(
                    "declared Python result does not have a return value data-dependent "
                    "on its function inputs"
                )

    if provenance == "package_x_derived":
        methods = result_meta.get("package_x_methods")
        evidence = result_meta.get("derivation_evidence")
        if not isinstance(methods, list) or not methods:
            errors.append("package_x_methods must be non-empty")
        if not isinstance(evidence, dict):
            errors.append("package_x_derived result lacks derivation_evidence")
        else:
            expected_evidence = {
                "observable": result_meta.get("observable"),
                "python_function": result_meta.get("python_function"),
                "package_x_methods": methods,
            }
            mismatches = [
                field
                for field, expected in expected_evidence.items()
                if evidence.get(field) != expected
            ]
            if source_wl is not None and evidence.get("source_wl_sha256") != _sha256(source_wl):
                mismatches.append("source_wl_sha256")
            if python_path is not None and evidence.get("python_file_sha256") != _sha256(python_path):
                mismatches.append("python_file_sha256")
            if mismatches:
                errors.append(
                    "derivation_evidence does not bind current metadata/artifacts: "
                    f"{sorted(set(mismatches))}"
                )
        if isinstance(methods, list) and methods and source_wl is not None:
            try:
                source_text = source_wl.read_text(encoding="utf-8")
                executable = _wolfram_live_executable_text(source_text)
            except (OSError, UnicodeError) as exc:
                errors.append(f"cannot read declared Wolfram artifact: {exc}")
            else:
                required_routes = {
                    "loop": (
                        "LoopIntegrate",
                        "LoopRefine",
                        "Projector",
                        "LoopRefineSeries",
                        "Transverse",
                        "Longitudinal",
                    ),
                    "tree": ("Spur", "Contract", "LoopRefine"),
                }
                route = required_routes.get(str(task_type))
                if route is not None and not any(
                    re.search(rf"\b{re.escape(method)}\s*\[", executable)
                    for method in route
                ):
                    errors.append(
                        f"{task_type} task has no executable Package-X "
                        f"{task_type} route ({', '.join(route)})"
                    )
                missing_calls = [
                    method
                    for method in methods
                    if not re.search(rf"\b{re.escape(str(method))}\s*\[", executable)
                ]
                if missing_calls:
                    errors.append(
                        "declared Package-X methods have no executable call outside "
                        f"comments/strings: {missing_calls}"
                    )
                if isinstance(evidence, dict) and not _wolfram_result_depends_on_package_x(
                    source_text,
                    methods=methods,
                    result_symbol=evidence.get("wolfram_result_symbol"),
                ):
                    errors.append(
                        "declared Wolfram result symbol is not data-dependent on a "
                        "declared Package-X method call"
                    )

    if isinstance(task, dict):
        target_quantity = task.get("target_quantity")
        observable = result_meta.get("observable")
        if target_quantity != observable:
            errors.append(
                f"result-meta observable {observable!r} does not match task "
                f"target_quantity {target_quantity!r}"
            )

    return errors
