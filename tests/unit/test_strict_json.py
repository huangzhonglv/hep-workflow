from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from scripts._strict_json import StrictJSONError, load_json, loads_json


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_non_finite_constants(token: str) -> None:
    with pytest.raises(StrictJSONError, match="non-finite numeric constant"):
        loads_json(f'{{"value": {token}}}', source="probe.json")


def test_strict_json_rejects_duplicate_keys_at_every_depth() -> None:
    with pytest.raises(StrictJSONError, match="duplicate object key: 'value'"):
        loads_json('{"outer": {"value": 1, "value": 2}}', source="probe.json")


@pytest.mark.parametrize("text", ['{"x": 1e400}', '{"x": -1e400}', '{"x": [1, {"y": 1e400}]}'])
def test_strict_json_rejects_decoded_numeric_overflow(text: str) -> None:
    with pytest.raises(StrictJSONError, match="non-finite decoded number"):
        loads_json(text, source="probe.json")


def test_strict_json_accepts_largest_finite_binary64() -> None:
    assert loads_json('{"x": 1.7976931348623157e308}')["x"] == 1.7976931348623157e308


@pytest.mark.parametrize(
    "token",
    ["1e-400", "-1e-400", "1e-9999999999999999999999999999999999999999"],
)
def test_strict_json_rejects_numeric_underflow(token: str) -> None:
    with pytest.raises(StrictJSONError, match="numeric underflow"):
        loads_json(f'{{"x": {token}}}', source="probe.json")


def test_strict_json_accepts_minimum_positive_subnormal() -> None:
    assert loads_json('{"x": 5e-324}')["x"] == float.fromhex(
        "0x0.0000000000001p-1022"
    )


@pytest.mark.parametrize("digits", [400, 5000])
def test_strict_json_rejects_integer_float_overflow(digits: int) -> None:
    token = "9" * digits
    with pytest.raises(StrictJSONError, match="integer exceeds"):
        loads_json(f'{{"x": {token}}}', source="huge-int.json")


def test_strict_json_wraps_excessive_nesting() -> None:
    text = "[" * 2000 + "0" + "]" * 2000
    with pytest.raises(StrictJSONError, match="nesting exceeds"):
        loads_json(text, source="deep.json")


def test_strict_json_wraps_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff")
    with pytest.raises(StrictJSONError, match="cannot read JSON file"):
        load_json(path)


def test_strict_json_accepts_unambiguous_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "valid.json"
    path.write_text('{"value": 1.25, "nested": {"ok": true}}\n', encoding="utf-8")

    assert load_json(path) == {"value": 1.25, "nested": {"ok": True}}


def test_hep_numerics_strict_loader_has_same_behavior(repo_root: Path) -> None:
    helper_path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "_strict_json.py"
    )
    spec = importlib.util.spec_from_file_location("hep_numerics_strict_json_test", helper_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(module.StrictJSONError, match="duplicate object key"):
        module.loads_json('{"x": 1, "x": 2}', source="scan-config.json")
    with pytest.raises(module.StrictJSONError, match="non-finite numeric constant"):
        module.loads_json('{"x": NaN}', source="scan-config.json")


def test_workspace_validator_rejects_duplicate_keys_before_schema_validation(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "duplicate-json"
    project.mkdir(parents=True)
    (project / "manifest.json").write_text(
        '{"project_name": "first", "project_name": "second"}\n',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(workspace),
            "duplicate-json",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "duplicate object key: 'project_name'" in completed.stdout
    assert "Traceback" not in completed.stderr


def test_workspace_validator_reports_excessive_nesting_without_traceback(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "deep-json"
    project.mkdir(parents=True)
    (project / "manifest.json").write_text(
        "[" * 2000 + "0" + "]" * 2000,
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(workspace),
            "deep-json",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "nesting exceeds" in completed.stdout
    assert "Traceback" not in completed.stderr
