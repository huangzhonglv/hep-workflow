from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_python_result(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(
        "smoke_e2e_result_python", path
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load Python result module from {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _parse_wolfram_json(stdout: str, stderr: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "BR_toy" in payload:
            return payload

    pytest.fail(
        "could not parse BR_toy JSON from wolframscript output\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )


@pytest.mark.e2e
def test_wolframscript_benchmark_matches_expected_smoke_e2e(
    wolframscript_required: str,
    smoke_e2e_project: Path,
    read_json,
) -> None:
    project_dir = smoke_e2e_project
    benchmark = next(
        entry
        for entry in read_json(project_dir / "model" / "benchmarks.json")[
            "benchmarks"
        ]
        if entry["task_id"] == "task-001"
    )
    test_point = benchmark["numerical_test_point"]
    inputs = test_point["inputs"]
    expected = float(test_point["expected_value"])
    tolerance = float(test_point["tolerance"])

    result_wl_path = project_dir / "calculations" / "task-001" / "result.wl"
    proc = subprocess.run(
        [wolframscript_required, "-script", str(result_wl_path)],
        input=json.dumps(inputs) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail(
            "wolframscript benchmark run failed with return code "
            f"{proc.returncode}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )

    output = _parse_wolfram_json(proc.stdout, proc.stderr)
    actual_wl = float(output["BR_toy"])
    assert abs(actual_wl - expected) <= tolerance, (
        "wolframscript BR_toy does not match benchmark expected value: "
        f"actual={actual_wl}, expected={expected}, tolerance={tolerance}"
    )

    result_py_path = (
        project_dir / "calculations" / "task-001" / "result-python.py"
    )
    result_py = _load_python_result(result_py_path)
    actual_py = float(result_py.compute_BR_toy(**inputs))
    assert abs(actual_py - expected) <= tolerance, (
        "Python BR_toy does not match benchmark expected value: "
        f"actual={actual_py}, expected={expected}, tolerance={tolerance}"
    )
    assert abs(actual_wl - actual_py) <= tolerance, (
        "wolframscript and Python BR_toy results differ: "
        f"wolframscript={actual_wl}, python={actual_py}, "
        f"tolerance={tolerance}"
    )
