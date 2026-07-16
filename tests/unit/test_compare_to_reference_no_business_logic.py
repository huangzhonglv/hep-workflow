from __future__ import annotations

from tests.unit.compare_reference_fixtures import make_compare_project, run_compare


def test_compare_requires_manifest_model_dependency_for_unit_binding(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path, manifest_text="")

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "manifest" in result.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()
