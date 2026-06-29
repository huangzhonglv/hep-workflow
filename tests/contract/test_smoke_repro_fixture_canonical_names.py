from __future__ import annotations

import ast
import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _custom_observables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("observable_"):
            names.add(node.name.removeprefix("observable_"))
            names.add(node.name)
    return names


def test_smoke_repro_fixture_uses_canonical_names(repo_root: Path) -> None:
    project = repo_root / "workspace" / "projects" / "smoke-e2e"
    model = _load_json(project / "model" / "model-spec.json")
    calc_tasks = _load_json(project / "model" / "calc-tasks.json")
    repro_targets = _load_json(project / "literature" / "repro-targets.json")
    paper_extract = _load_json(project / "literature" / "paper-extract.json")

    parameters = {item["name"] for item in model["parameters"]}
    observables = {task["target_quantity"] for task in calc_tasks["tasks"]}
    observables |= _custom_observables(project / "numerics" / "custom_observables.py")

    for target in repro_targets["targets"]:
        assert target["x_param"] in parameters, target
        assert target["y_param"] in parameters or target["y_param"] in observables, target
        for name in target["fixed"]:
            assert name in parameters, target
        for observable in target["observables"]:
            assert observable in observables, target

        canonical_only_values = [
            target["x_param"],
            target["y_param"],
            *target["fixed"].keys(),
            *target["observables"],
        ]
        for value in canonical_only_values:
            assert " " not in value
            assert value != "Toy BR upper limit"

    hints = paper_extract["scan_config_hints"]
    assert {hint["target_id"] for hint in hints} == {"target-001", "target-002"}
    for hint in hints:
        for scan_parameter in hint["scan_parameters"]:
            assert scan_parameter["canonical_name"] in parameters, hint
        for fixed_name in hint["fixed_parameters"]:
            assert fixed_name in parameters, hint

    target_002 = next(hint for hint in hints if hint["target_id"] == "target-002")
    assert target_002["missing_fields"] == ["v_Delta_value"]
