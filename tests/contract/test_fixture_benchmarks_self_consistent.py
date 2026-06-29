"""Contract: fixture benchmark numerical points match result-python.py.

Catches fixture math errors, such as expected_value=4e-11 when the
Python result evaluates to 4e-12, before wolframscript-gated e2e tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOTS = (
    ("workspace", REPO_ROOT / "workspace" / "projects"),
    ("test-fixtures", REPO_ROOT / "tests" / "fixtures" / "workspace-projects"),
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _python_function_for_task(
    proj_dir: Path, task_id: str, task: dict[str, Any], observable: str
) -> str | None:
    # Prefer calc-tasks.json if future fixtures carry this field. Current
    # schemas keep executable Python metadata in result-meta.json.
    fn_name = task.get("python_function")
    if isinstance(fn_name, str) and fn_name:
        return fn_name

    meta_path = proj_dir / "calculations" / task_id / "result-meta.json"
    if meta_path.exists():
        meta = _read_json(meta_path)
        fn_name = meta.get("python_function")
        if isinstance(fn_name, str) and fn_name:
            return fn_name

    if observable and observable != "?":
        return f"compute_{observable}"
    return None


def _iter_fixture_projects():
    for root_label, root in FIXTURE_ROOTS:
        if not root.exists():
            continue
        for proj_dir in sorted(root.iterdir()):
            if proj_dir.is_dir():
                yield root_label, proj_dir


def _collect_benchmark_cases() -> tuple[list[Any], int, set[str]]:
    params: list[Any] = []
    executable_count = 0
    discovered_projects: set[str] = set()

    for root_label, proj_dir in _iter_fixture_projects():
        fixture_label = f"{root_label}/{proj_dir.name}"
        discovered_projects.add(fixture_label)

        bench_path = proj_dir / "model" / "benchmarks.json"
        tasks_path = proj_dir / "model" / "calc-tasks.json"
        if not bench_path.exists() or not tasks_path.exists():
            continue

        bench = _read_json(bench_path)
        tasks = _read_json(tasks_path)
        task_by_id = {
            task["task_id"]: task for task in tasks.get("tasks", [])
        }

        for entry in bench.get("benchmarks", []):
            if not entry.get("has_benchmark"):
                continue
            numerical_test_point = entry.get("numerical_test_point")
            if not numerical_test_point:
                continue

            task_id = entry["task_id"]
            task = task_by_id.get(task_id, {})
            observable = (
                entry.get("observable")
                or task.get("target_quantity")
                or "?"
            )
            inputs = numerical_test_point["inputs"]
            expected = numerical_test_point["expected_value"]
            tolerance = numerical_test_point["tolerance"]
            rp_path = (
                proj_dir
                / "calculations"
                / task_id
                / "result-python.py"
            )
            fn_name = _python_function_for_task(
                proj_dir, task_id, task, observable
            )

            marks = []
            if not rp_path.exists():
                marks.append(pytest.mark.skip(reason="missing result-python.py"))
            if not fn_name:
                marks.append(
                    pytest.mark.skip(reason="missing python_function mapping")
                )
            if _as_float(expected) is None:
                marks.append(
                    pytest.mark.skip(reason="non-numeric expected_value")
                )
            if _as_float(tolerance) is None:
                marks.append(
                    pytest.mark.skip(reason="non-numeric absolute tolerance")
                )

            if not marks:
                executable_count += 1

            params.append(
                pytest.param(
                    fixture_label,
                    task_id,
                    observable,
                    inputs,
                    expected,
                    tolerance,
                    str(rp_path),
                    fn_name or "",
                    id=f"{fixture_label}/{task_id}/{observable}",
                    marks=marks,
                )
            )

    return params, executable_count, discovered_projects


(
    BENCHMARK_CASES,
    EXECUTABLE_BENCHMARK_CASE_COUNT,
    DISCOVERED_FIXTURE_PROJECTS,
) = _collect_benchmark_cases()


def _module_name_for(rp_path: str, fn_name: str) -> str:
    raw = f"_fixture_rp_{rp_path}_{fn_name}"
    return "".join(ch if ch.isalnum() else "_" for ch in raw)


def _load_fn(rp_path: str, fn_name: str):
    spec = importlib.util.spec_from_file_location(
        _module_name_for(rp_path, fn_name), rp_path
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load Python result module from {rp_path}")

    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous

    try:
        return getattr(module, fn_name)
    except AttributeError:
        pytest.fail(f"{rp_path} does not define {fn_name}")


def test_collects_at_least_one_executable_benchmark_case() -> None:
    assert EXECUTABLE_BENCHMARK_CASE_COUNT >= 1


def test_collects_synthetic_workspace_fixture_root() -> None:
    assert "workspace/smoke-e2e" in DISCOVERED_FIXTURE_PROJECTS
    assert "test-fixtures/numerics-contract" in DISCOVERED_FIXTURE_PROJECTS


@pytest.mark.parametrize(
    "project,task_id,observable,inputs,expected,tolerance,rp_path,fn_name",
    BENCHMARK_CASES,
)
def test_benchmark_python_matches_expected(
    project: str,
    task_id: str,
    observable: str,
    inputs: dict[str, Any],
    expected: float,
    tolerance: float,
    rp_path: str,
    fn_name: str,
) -> None:
    expected_float = float(expected)
    tolerance_float = float(tolerance)
    fn = _load_fn(rp_path, fn_name)
    try:
        actual = float(fn(**inputs))
    except Exception as exc:  # pragma: no cover - failure context path
        pytest.fail(
            f"{project}/{task_id}/{observable}: "
            f"failed to evaluate {fn_name} with inputs={inputs} "
            f"expected={expected_float} tolerance={tolerance_float} "
            f"error={exc!r}"
        )

    abs_diff = abs(actual - expected_float)
    assert abs_diff <= tolerance_float, (
        f"{project}/{task_id}/{observable}: "
        f"inputs={inputs} expected={expected_float} actual={actual} "
        f"abs_diff={abs_diff} tolerance={tolerance_float}"
    )
