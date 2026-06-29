from __future__ import annotations

from tests.unit.compare_reference_fixtures import default_target, load_result, make_compare_project, run_compare


def test_blocked_targets_emit_blocked_result_and_overlay_only(repo_root, tmp_path) -> None:
    targets = [
        default_target("fig-3a"),
        default_target("fig-5b"),
    ]
    project_dir = make_compare_project(tmp_path, targets=targets)

    result = run_compare(repo_root, project_dir, "run-001", "--blocked-targets", "fig-3a")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = load_result(project_dir, "run-001")
    by_id = {item["target_id"]: item for item in payload["results"]}

    blocked = by_id["fig-3a"]
    assert blocked["verdict"] == "blocked"
    assert blocked["verdict_ceiling"] == "needs_human_review"
    assert any("blocked_by_orchestrator" in warning for warning in blocked["warnings"])
    assert (project_dir / blocked["generated_files"]["overlay"]["pdf"]).exists()
    assert (project_dir / blocked["generated_files"]["overlay"]["png"]).exists()
    assert not (project_dir / blocked["generated_files"]["side_by_side"]["pdf"]).exists()
    assert not (project_dir / blocked["generated_files"]["residual"]["pdf"]).exists()

    normal = by_id["fig-5b"]
    assert normal["verdict"] == "pass"
    for file_pair in normal["generated_files"].values():
        assert (project_dir / file_pair["pdf"]).exists()
        assert (project_dir / file_pair["png"]).exists()
