from __future__ import annotations

import json
import shutil
import subprocess
import sys


def reset_numerics_dirs(project_dir) -> None:
    for name in ("scan-configs", "scan-results", "figures"):
        target = project_dir / "numerics" / name
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)


def test_init_analysis_reuses_existing_unexecuted_draft(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)

    first = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stdout + first.stderr

    draft_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    assert draft_path.exists()

    draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    draft_payload["seed"] = 99
    draft_path.write_text(json.dumps(draft_payload, indent=2) + "\n", encoding="utf-8")

    second = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Reused existing unexecuted draft scan-config" in second.stdout
    assert not (project_dir / "numerics" / "scan-configs" / "analysis-002.json").exists()

    refreshed_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert refreshed_payload["analysis_id"] == "analysis-001"
    assert refreshed_payload["description"].startswith("Draft scan-config for ")
    assert refreshed_payload["seed"] == 0


def test_init_analysis_allocates_new_id_once_existing_draft_has_results(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)

    first = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert (project_dir / "numerics" / "scan-configs" / "analysis-001.json").exists()

    results_dir = project_dir / "numerics" / "scan-results" / "analysis-001"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "scan.csv").write_text("x\n1\n", encoding="utf-8")

    second = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Wrote draft scan-config" in second.stdout
    assert (project_dir / "numerics" / "scan-configs" / "analysis-002.json").exists()
