from __future__ import annotations

import copy

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from scripts._scan_artifact_validation import scan_execution_snapshot


def test_interpolated_constraint_schema_rejects_blank_unit(
    read_json,
    repo_root,
) -> None:
    schema = read_json(repo_root / "schemas" / "constraints-data.schema.json")
    payload = copy.deepcopy(
        read_json(repo_root / "schemas" / "examples" / "constraints-data.example.json")
    )
    payload["constraints"][0]["unit"] = "   "

    issues = list(Draft202012Validator(schema).iter_errors(payload))

    assert any(list(issue.absolute_path)[-1:] == ["unit"] for issue in issues)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("wrong_x,y\n1,2\n2,3\n", "missing configured columns"),
        ("x,y\n1,2\n1,3\n", "x nodes must be unique"),
        ("x,y\n2,2\n1,3\n", "strictly increasing"),
        ("x,y\n1,nan\n2,3\n", "non-finite"),
        ("1,2\n2,3\n", "missing configured columns"),
    ],
)
def test_interpolation_csv_requires_explicit_finite_ordered_columns(
    tmp_path,
    run_scan_module,
    content,
    message,
) -> None:
    path = tmp_path / "limit.csv"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        run_scan_module.read_xy_csv(path, "x", "y")


def test_local_rng_has_stable_phase_and_point_substreams(run_scan_module) -> None:
    first = run_scan_module.local_rng(
        12345,
        phase="scan",
        point_index=7,
        consumer="observable_x",
    ).random(8)
    repeated = run_scan_module.local_rng(
        12345,
        phase="scan",
        point_index=7,
        consumer="observable_x",
    ).random(8)
    smoke = run_scan_module.local_rng(
        12345,
        phase="smoke",
        point_index=0,
        consumer="observable_x",
    ).random(8)
    other_point = run_scan_module.local_rng(
        12345,
        phase="scan",
        point_index=8,
        consumer="observable_x",
    ).random(8)

    assert np.array_equal(first, repeated)
    assert not np.array_equal(first, smoke)
    assert not np.array_equal(first, other_point)


def test_task_output_context_is_immutable_and_exactly_keyed(run_scan_module) -> None:
    runtime = {
        "task_backends": {"task-001": lambda *, x: x * 2.0},
        "task_parameter_names": {"task-001": {"x"}},
    }

    context = run_scan_module.build_task_output_context(runtime, ["task-001"])

    assert context.api_version == "hep-task-callables-v1"
    assert list(context) == ["task-001"]
    assert context["task-001"](x=1.5) == pytest.approx(3.0)
    with pytest.raises(KeyError):
        _ = context["task-999"]
    with pytest.raises(TypeError):
        context["task-001"] = lambda **_: 0.0  # type: ignore[index]


def test_task_output_context_rejects_unknown_and_nonfinite_inputs(
    run_scan_module,
) -> None:
    runtime = {
        "task_backends": {"task-001": lambda *, x=1.0: x},
        "task_parameter_names": {"task-001": {"x"}},
    }
    context = run_scan_module.build_task_output_context(runtime, ["task-001"])

    with pytest.raises(TypeError, match="undeclared parameters"):
        context["task-001"](typo=2.0)
    with pytest.raises(ValueError, match="must be finite"):
        context["task-001"](x=float("nan"))


def test_single_return_task_cannot_be_rebound_to_two_observable_names(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_module,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = read_json(config_path)
    config["observables"].append(
        {
            "observable": "Mislabelled_task_alias",
            "source": {"type": "task", "task_id": "task-001"},
        }
    )
    write_json(config_path, config)

    validation = run_scan_module.validate(
        run_scan_module.load_inputs(
            project_dir=project_dir,
            analysis_id="analysis-001",
        )
    )

    check = next(
        item for item in validation["report"].checks if item.code == "NUM-PREFLIGHT-004"
    )
    assert check.status == "FAIL"
    assert any("multiple observable names" in detail for detail in check.details)


def test_constraint_task_observable_must_match_its_single_return_contract(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_module,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = read_json(constraints_path)
    constraint = next(item for item in constraints["constraints"] if item["id"] == "c-001")
    constraint["observable"] = "Mislabelled_task_alias"
    write_json(constraints_path, constraints)

    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = read_json(config_path)
    config["constraints_used"] = ["c-001"]
    write_json(config_path, config)

    validation = run_scan_module.validate(
        run_scan_module.load_inputs(
            project_dir=project_dir,
            analysis_id="analysis-001",
        )
    )

    check = next(
        item for item in validation["report"].checks if item.code == "NUM-PREFLIGHT-006"
    )
    assert check.status == "FAIL"
    assert any("incompatible observable names" in detail for detail in check.details)


def test_only_title_changes_are_renderer_only(read_json, repo_root) -> None:
    original = read_json(repo_root / "schemas" / "examples" / "scan-config.example.json")
    title_only = {
        **original,
        "figures": [dict(item) for item in original["figures"]],
    }
    title_only["figures"][0]["title"] = "A revised presentation title"
    semantic_change = {
        **original,
        "figures": [dict(item) for item in original["figures"]],
    }
    semantic_change["figures"][0]["show_allowed_region"] = False

    assert scan_execution_snapshot(original) == scan_execution_snapshot(title_only)
    assert scan_execution_snapshot(original) != scan_execution_snapshot(semantic_change)


def test_ambient_rng_source_is_rejected_but_injected_rng_is_allowed(
    tmp_path,
    run_scan_module,
) -> None:
    ambient = tmp_path / "ambient.py"
    ambient.write_text(
        "import numpy as np\n\ndef observable(*, x):\n    return np.random.random()\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.py"
    explicit.write_text(
        "def observable(*, rng, x):\n    return rng.random()\n",
        encoding="utf-8",
    )

    assert any("ambient NumPy RNG" in issue for issue in run_scan_module.ambient_rng_source_issues(ambient))
    assert run_scan_module.ambient_rng_source_issues(explicit) == []


@pytest.mark.parametrize(
    "source",
    [
        "from numpy import random as entropy\ndef observable(*, x):\n    return entropy.random()\n",
        "import os as operating_system\ndef observable(*, x):\n    return float(operating_system.urandom(1)[0])\n",
        "from uuid import uuid4 as fresh\ndef observable(*, x):\n    return float(fresh().int)\n",
        "def observable(*, x):\n    return __import__('random').random()\n",
        "import importlib as loader\ndef observable(*, x):\n    return loader.import_module('random').random()\n",
        "import builtins as language\ndef observable(*, x):\n    return language.__import__('random').random()\n",
        "import numpy as np\ndef observable(*, x):\n    return getattr(np, 'random').random()\n",
        "import numpy as np\ndef observable(*, x):\n    return np.__dict__['random'].random()\n",
    ],
)
def test_ambient_rng_aliases_and_dynamic_imports_are_rejected(
    tmp_path,
    run_scan_module,
    source,
) -> None:
    path = tmp_path / "concealed_entropy.py"
    path.write_text(source, encoding="utf-8")

    assert run_scan_module.ambient_rng_source_issues(path)


def test_scan_config_exact_source_preserves_crlf_bytes(
    tmp_path,
    project_copy_factory,
    run_scan_module,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    crlf_bytes = config_path.read_bytes().replace(b"\n", b"\r\n")
    config_path.write_bytes(crlf_bytes)

    inputs = run_scan_module.load_inputs(
        project_dir=project_dir,
        analysis_id="analysis-001",
    )

    assert inputs["scan_config_bytes"] == crlf_bytes
    assert inputs["scan_config_source"].encode("utf-8") == crlf_bytes
