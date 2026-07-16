from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

from scripts._dependency_graph import build_dependency_graph, make_spec


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_HELPER = (
    REPO_ROOT / ".agents" / "skills" / "hep-numerics" / "scripts" / "_manifest.py"
)


def load_manifest_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("manifest_v2_test_helper", MANIFEST_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_migration_cli() -> ModuleType:
    path = REPO_ROOT / "scripts" / "migrate_manifest_v2.py"
    spec = importlib.util.spec_from_file_location(
        "manifest_v2_migration_cleanup_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def dependency(version: str, checksum_digit: str, tasks: list[str]) -> dict[str, object]:
    checksum = f"sha256:{checksum_digit * 64}"
    return {
        "model": {"version": version, "checksum": checksum},
        "calculations": {"tasks": tasks, "model_version": version},
        "constraints": {"checksum": f"sha256:{'c' * 64}"},
    }


def analysis(
    analysis_id: str,
    *,
    status: str = "done",
    version: str = "v1",
    checksum_digit: str = "1",
    timestamp: str = "2026-07-13T00:00:00Z",
) -> dict[str, object]:
    return {
        "analysis_id": analysis_id,
        "status": status,
        "files": [f"numerics/scan-configs/{analysis_id}.json"],
        "depends_on": dependency(version, checksum_digit, ["task-001"]),
        "produced_by": "hep-numerics",
        "timestamp": timestamp,
    }


def legacy_manifest_from_v2(v2: dict[str, object]) -> dict[str, object]:
    legacy = deepcopy(v2)
    legacy.pop("manifest_version")
    entry = legacy["artifacts"]["numerics"]["analyses"][0]
    legacy["artifacts"]["numerics"] = {
        "status": entry["status"],
        "files": list(entry["files"]),
        "depends_on": deepcopy(entry["depends_on"]),
        "analyses": [entry["analysis_id"]],
        "produced_by": entry["produced_by"],
        "timestamp": entry["timestamp"],
    }
    return legacy


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([], "not_started"),
        (["done", "done"], "done"),
        (["done", "partial"], "partial"),
        (["done", "stale"], "stale"),
        (["stale", "blocked"], "blocked"),
        (["blocked", "failed"], "failed"),
    ],
)
def test_aggregate_status_is_conservative(statuses: list[str], expected: str) -> None:
    helper = load_manifest_helper()
    entries = [analysis(f"analysis-{index:03d}", status=status) for index, status in enumerate(statuses, 1)]

    assert helper.aggregate_numerics_status(entries) == expected


def test_merge_preserves_heterogeneous_entries_and_is_idempotent() -> None:
    helper = load_manifest_helper()
    old = analysis("analysis-001", version="v1", checksum_digit="1")
    current = analysis(
        "analysis-002",
        version="v2",
        checksum_digit="2",
        timestamp="2026-07-13T00:00:01Z",
    )
    existing = helper.derive_numerics_artifact([old])
    active_model = {
        "version": "v2",
        "checksum": f"sha256:{'2' * 64}",
    }

    merged = helper.merge_numerics_analysis(
        existing,
        current,
        active_model=active_model,
        constraints_checksum=f"sha256:{'c' * 64}",
    )
    repeated = helper.merge_numerics_analysis(
        merged,
        current,
        active_model=active_model,
        constraints_checksum=f"sha256:{'c' * 64}",
    )

    assert repeated == merged
    assert [item["analysis_id"] for item in merged["analyses"]] == [
        "analysis-001",
        "analysis-002",
    ]
    assert merged["analyses"][0]["status"] == "stale"
    assert merged["analyses"][0]["depends_on"] == old["depends_on"]
    assert merged["analyses"][1] == current
    assert merged["status"] == "stale"
    assert merged["files"] == sorted(
        {path for item in merged["analyses"] for path in item["files"]}
    )


def test_derive_rejects_duplicate_semantic_ids() -> None:
    helper = load_manifest_helper()
    duplicate = analysis("analysis-001")
    conflicting = deepcopy(duplicate)
    conflicting["status"] = "partial"

    with pytest.raises(ValueError, match="duplicate numerics analysis_id"):
        helper.derive_numerics_artifact([duplicate, conflicting])


@pytest.mark.parametrize(
    "status,expected",
    [
        ("done", "stale"),
        ("partial", "stale"),
        ("in_progress", "in_progress"),
        ("failed", "failed"),
        ("blocked", "blocked"),
        ("skipped", "skipped"),
        ("not_started", "not_started"),
    ],
)
def test_staleness_refresh_only_reclassifies_current_looking_evidence(
    status: str,
    expected: str,
) -> None:
    helper = load_manifest_helper()
    entry = analysis("analysis-001", status=status, version="v1", checksum_digit="1")

    refreshed = helper._mark_stale_against_active_inputs(
        [entry],
        active_model={"version": "v2", "checksum": f"sha256:{'2' * 64}"},
        constraints_checksum=f"sha256:{'d' * 64}",
    )

    assert refreshed[0]["status"] == expected


def test_replot_candidate_owns_final_paths_but_uses_staged_evidence(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    manifest = read_json(project_dir / "manifest.json")
    analysis_id = "analysis-001"
    config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    config = read_json(config_path)
    config["figures"] = [
        {
            "kind": "scan_1d",
            "x": "M_Hpp",
            "observables": ["Br_mu_to_egamma"],
            "overlay_constraint_bands": True,
        }
    ]
    write_json(config_path, config)
    constraints_payload = read_json(project_dir / "constraints" / "constraints-data.json")
    constraints = {item["id"]: item for item in constraints_payload["constraints"]}
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    basename = "scan1d-M_Hpp-Br_mu_to_egamma"
    staged_paths = [staged_dir / f"{basename}.{suffix}" for suffix in ("pdf", "png")]
    for path in staged_paths:
        path.write_bytes(b"validated staged figure")
    final_paths = [
        project_dir / "numerics" / "figures" / analysis_id / path.name
        for path in staged_paths
    ]
    original_dependencies = deepcopy(
        manifest["artifacts"]["numerics"]["analyses"][0]["depends_on"]
    )

    candidate_kwargs = {
        "project_dir": project_dir,
        "analysis_id": analysis_id,
        "scan_config": config,
        "constraints_by_id": constraints,
        "scan_config_path": config_path,
        "scan_csv_path": project_dir
        / "numerics"
        / "scan-results"
        / analysis_id
        / "scan.csv",
        "scan_meta_path": project_dir
        / "numerics"
        / "scan-results"
        / analysis_id
        / "scan.meta.json",
        "analysis_summary_path": project_dir
        / "numerics"
        / f"analysis-summary-{analysis_id}.md",
        "figure_paths": final_paths,
        "figure_evidence_paths": staged_paths,
        "allow_unpublished_files": True,
        "history_action": "numerics_figures_regenerated",
        "timestamp": "2026-07-13T00:00:01Z",
    }
    with pytest.raises(ValueError, match="require a fresh"):
        helper.build_manifest_for_numerics(manifest, **candidate_kwargs)

    candidate = helper.build_manifest_for_numerics(
        manifest,
        history_event_id="1" * 32,
        **candidate_kwargs,
    )
    with pytest.raises(ValueError, match="duplicate manifest history event_id"):
        helper.build_manifest_for_numerics(
            candidate,
            history_event_id="1" * 32,
            **candidate_kwargs,
        )

    entry = candidate["artifacts"]["numerics"]["analyses"][0]
    assert entry["status"] == "done"
    assert entry["depends_on"] == original_dependencies
    for path in final_paths:
        assert path.relative_to(project_dir).as_posix() in entry["files"]
        assert not path.exists()


def test_manifest_serializer_refuses_live_paths(tmp_path: Path) -> None:
    helper = load_manifest_helper()
    live_path = tmp_path / "manifest.json"

    with pytest.raises(ValueError, match="transaction staging"):
        helper._write_staged_manifest_candidate(live_path, {"not": "validated"})
    assert not live_path.exists()

    staged_path = (
        tmp_path
        / ".hep-workflow-transactions"
        / "tx-probe"
        / "staging"
        / "manifest.json"
    )
    staged_path.parent.mkdir(parents=True)
    helper._write_staged_manifest_candidate(staged_path, {"staged": True})
    assert json.loads(staged_path.read_text(encoding="utf-8")) == {"staged": True}


def test_v1_migration_is_explicit_deterministic_and_idempotent(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    rebind_scan_result,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    rebind_scan_result(project_dir)
    v2 = read_json(project_dir / "manifest.json")
    entry = v2["artifacts"]["numerics"]["analyses"][0]
    legacy = deepcopy(v2)
    legacy.pop("manifest_version")
    legacy["artifacts"]["numerics"] = {
        "status": entry["status"],
        "files": list(entry["files"]),
        "depends_on": deepcopy(entry["depends_on"]),
        "analyses": [entry["analysis_id"]],
        "produced_by": entry["produced_by"],
        "timestamp": entry["timestamp"],
    }

    migrated = helper.migrate_manifest_v1(legacy, project_dir=project_dir)

    assert migrated == v2
    assert helper.migrate_manifest_v1(migrated, project_dir=project_dir) == migrated


def test_v1_migration_reconciles_every_legacy_aggregate_field(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    rebind_scan_result,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    rebind_scan_result(project_dir)
    v2 = read_json(project_dir / "manifest.json")

    legacy = legacy_manifest_from_v2(v2)
    legacy["artifacts"]["numerics"]["status"] = "partial"
    with pytest.raises(ValueError, match="aggregate status"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)

    legacy = legacy_manifest_from_v2(v2)
    legacy["artifacts"]["numerics"]["depends_on"]["model"]["version"] = "v999"
    with pytest.raises(ValueError, match="aggregate depends_on"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)

    legacy = legacy_manifest_from_v2(v2)
    legacy["artifacts"]["numerics"]["produced_by"] = "different-producer"
    with pytest.raises(ValueError, match="aggregate produced_by"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)

    legacy = legacy_manifest_from_v2(v2)
    legacy["artifacts"]["numerics"]["timestamp"] = "2026-07-13T00:00:00Z"
    with pytest.raises(ValueError, match="aggregate timestamp"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)

    extra_path = project_dir / "numerics" / "legacy-extra.txt"
    extra_path.write_text("legacy evidence\n", encoding="utf-8")
    legacy = legacy_manifest_from_v2(v2)
    legacy["artifacts"]["numerics"]["files"].append("numerics/legacy-extra.txt")
    with pytest.raises(ValueError, match="discarding unowned legacy files"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)


def test_v1_migration_rejects_schema_invalid_scan_metadata(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_scan_result,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    rebind_scan_result(project_dir)
    legacy = legacy_manifest_from_v2(read_json(project_dir / "manifest.json"))
    meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-001"
        / "scan.meta.json"
    )
    metadata = read_json(meta_path)
    metadata["schema_forbidden_extra"] = True
    write_json(meta_path, metadata)

    with pytest.raises(ValueError, match="failed scan-meta.schema.json"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)


def test_v1_migration_rejects_incomplete_recorded_scan_graph(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_scan_result,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    rebind_scan_result(project_dir)
    legacy = legacy_manifest_from_v2(read_json(project_dir / "manifest.json"))
    meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-001"
        / "scan.meta.json"
    )
    metadata = read_json(meta_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    metadata["input_provenance"] = build_dependency_graph(
        project_dir,
        REPO_ROOT,
        [
            make_spec(
                "project",
                "constraints-data",
                project_dir,
                constraints_path,
            )
        ],
    )
    write_json(meta_path, metadata)

    with pytest.raises(ValueError, match="cannot derive expected scan provenance"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)


def test_v1_migration_fails_closed_without_analysis_history(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    rebind_scan_result,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    rebind_scan_result(project_dir)
    current = read_json(project_dir / "manifest.json")
    entry = current["artifacts"]["numerics"]["analyses"][0]
    legacy = deepcopy(current)
    legacy.pop("manifest_version")
    legacy["artifacts"]["numerics"] = {
        "status": entry["status"],
        "files": list(entry["files"]),
        "depends_on": deepcopy(entry["depends_on"]),
        "analyses": [entry["analysis_id"]],
        "produced_by": entry["produced_by"],
        "timestamp": entry["timestamp"],
    }
    legacy["history"] = [
        entry for entry in legacy["history"] if not entry["action"].startswith("numerics_")
    ]

    with pytest.raises(ValueError, match="no analysis-scoped numerics history"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)


def test_v1_migration_rejects_empty_registry_with_nonempty_legacy_state(
    tmp_path: Path,
    project_copy_factory,
    read_json,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    legacy = read_json(project_dir / "manifest.json")
    legacy.pop("manifest_version")
    legacy["artifacts"]["numerics"] = {
        "status": "partial",
        "files": ["numerics/scan-results/analysis-001/scan.csv"],
        "depends_on": dependency("v1", "1", ["task-001"]),
        "analyses": [],
        "produced_by": "legacy-writer",
        "timestamp": "2026-07-13T00:00:00Z",
    }

    with pytest.raises(ValueError, match="exact not_started empty skeleton"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)


def test_v1_migration_rejects_nonfinite_intrinsic_scan_evidence(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
) -> None:
    helper = load_manifest_helper()
    project_dir = project_copy_factory(tmp_path)
    manifest = read_json(project_dir / "manifest.json")
    entry = manifest["artifacts"]["numerics"]["analyses"][0]
    legacy = deepcopy(manifest)
    legacy.pop("manifest_version")
    legacy["artifacts"]["numerics"] = {
        "status": entry["status"],
        "files": list(entry["files"]),
        "depends_on": deepcopy(entry["depends_on"]),
        "analyses": [entry["analysis_id"]],
        "produced_by": entry["produced_by"],
        "timestamp": entry["timestamp"],
    }
    csv_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-001"
        / "scan.csv"
    )
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    cells = rows[1].split(",")
    cells[2] = "NaN"
    rows[1] = ",".join(cells)
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    meta_path = csv_path.with_name("scan.meta.json")
    meta = read_json(meta_path)
    meta["scan_csv_sha256"] = helper.file_sha256(csv_path)
    write_json(meta_path, meta)

    with pytest.raises(ValueError, match="intrinsic scan evidence is invalid"):
        helper.migrate_manifest_v1(legacy, project_dir=project_dir)


def test_migration_cli_is_read_only_by_default_and_publishes_explicitly(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)
    v2 = read_json(project_dir / "manifest.json")
    entry = v2["artifacts"]["numerics"]["analyses"][0]
    legacy = deepcopy(v2)
    legacy.pop("manifest_version")
    legacy["artifacts"]["numerics"] = {
        "status": entry["status"],
        "files": list(entry["files"]),
        "depends_on": deepcopy(entry["depends_on"]),
        "analyses": [entry["analysis_id"]],
        "produced_by": entry["produced_by"],
        "timestamp": entry["timestamp"],
    }
    manifest_path = project_dir / "manifest.json"
    write_json(manifest_path, legacy)
    before = manifest_path.read_bytes()
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "migrate_manifest_v2.py"),
        "--project-dir",
        str(project_dir),
    ]

    check = subprocess.run(command, capture_output=True, text=True)
    assert check.returncode == 1
    assert "NEEDS MIGRATION" in check.stdout
    assert manifest_path.read_bytes() == before
    assert not (project_dir / ".hep-workflow-transactions").exists()

    migrate = subprocess.run(command + ["--write"], capture_output=True, text=True)
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr
    assert read_json(manifest_path) == v2
    assert not (project_dir / ".hep-workflow-transactions").exists()


def test_migration_candidate_requires_complete_workspace_semantics(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    rebind_calculation_result,
    rebind_scan_result,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)
    candidate = read_json(project_dir / "manifest.json")
    candidate["artifacts"]["numerics"]["files"] = [
        "numerics/scan-configs/analysis-001.json"
    ]
    migration = load_migration_cli()
    helper = load_manifest_helper()

    with pytest.raises(ValueError, match="authoritative workspace validation"):
        migration.validate_candidate(helper, REPO_ROOT, project_dir, candidate)


def test_committed_migration_cleanup_warning_is_success_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_copy_factory,
    read_json,
    write_json,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    legacy = read_json(project_dir / "manifest.json")
    legacy.pop("manifest_version")
    legacy["history"] = [
        entry
        for entry in legacy["history"]
        if not entry["action"].startswith("numerics_")
    ]
    legacy["artifacts"]["numerics"] = {
        "status": "not_started",
        "files": [],
        "depends_on": {
            "model": {"version": None, "checksum": None},
            "calculations": {"tasks": [], "model_version": None},
            "constraints": {"checksum": None},
        },
        "analyses": [],
        "produced_by": None,
        "timestamp": None,
    }
    expected_v2 = deepcopy(legacy)
    expected_v2["manifest_version"] = 2
    expected_v2["artifacts"]["numerics"].pop("depends_on")
    manifest_path = project_dir / "manifest.json"
    write_json(manifest_path, legacy)
    migration = load_migration_cli()
    original_commit = migration.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise migration.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        migration.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )
    monkeypatch.setattr(migration, "validate_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(REPO_ROOT / "scripts" / "migrate_manifest_v2.py"),
            "--project-dir",
            str(project_dir),
            "--write",
        ],
    )

    assert migration.main() == 0
    warning = capsys.readouterr().err
    assert "committed successfully" in warning
    assert "Do not retry" in warning
    assert "injected cleanup interruption" in warning
    assert read_json(manifest_path) == expected_v2
