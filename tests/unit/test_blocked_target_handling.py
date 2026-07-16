from __future__ import annotations

from tests.unit.compare_reference_fixtures import (
    default_target,
    load_result,
    make_compare_project,
    mark_scan_hint_blocked,
    run_compare,
)


def test_blocked_targets_emit_blocked_result_and_overlay_only(repo_root, tmp_path) -> None:
    targets = [
        default_target("fig-3a"),
        default_target("fig-5b"),
    ]
    project_dir = make_compare_project(tmp_path, targets=targets)
    mark_scan_hint_blocked(project_dir, "fig-3a")

    result = run_compare(repo_root, project_dir, "run-001", "--blocked-targets", "fig-3a")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = load_result(project_dir, "run-001")
    by_id = {item["target_id"]: item for item in payload["results"]}

    blocked = by_id["fig-3a"]
    assert blocked["verdict"] == "blocked"
    assert blocked["verdict_ceiling"] == "needs_human_review"
    assert any("blocked_by_orchestrator" in warning for warning in blocked["warnings"])
    assert set(blocked["generated_files"]) == {"overlay"}
    assert (project_dir / blocked["generated_files"]["overlay"]["pdf"]).exists()
    assert (project_dir / blocked["generated_files"]["overlay"]["png"]).exists()

    normal = by_id["fig-5b"]
    assert normal["derivation_independence"] == "unknown"
    assert normal["verdict_ceiling"] == "needs_human_review"
    assert normal["verdict"] == "needs_human_review"
    assert set(normal["generated_files"]) == {"overlay", "side_by_side", "residual"}
    for file_pair in normal["generated_files"].values():
        assert (project_dir / file_pair["pdf"]).exists()
        assert (project_dir / file_pair["png"]).exists()
