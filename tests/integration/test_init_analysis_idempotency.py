from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def load_init_analysis_module(repo_root):
    path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "init_analysis.py"
    )
    spec = importlib.util.spec_from_file_location(
        "hep_numerics_init_analysis_cleanup_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reset_numerics_dirs(project_dir) -> None:
    for name in ("scan-configs", "scan-results", "figures"):
        target = project_dir / "numerics" / name
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
    for summary in (project_dir / "numerics").glob("analysis-summary-analysis-*.md"):
        summary.unlink()
    manifest_path = project_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["numerics"] = {
        "status": "not_started",
        "files": [],
        "analyses": [],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


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

    before_custom = (project_dir / "numerics" / "custom_observables.py").read_bytes()
    failed_env = os.environ.copy()
    failed_env["HEP_WORKFLOW_TEST_FAIL_ANALYSIS_INIT_AFTER"] = "analysis-001.json"
    failed = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--reuse-draft",
        ],
        capture_output=True,
        text=True,
        env=failed_env,
    )
    assert failed.returncode != 0
    assert "injected analysis initializer failure" in failed.stderr
    assert json.loads(draft_path.read_text(encoding="utf-8"))["seed"] == 99
    assert (project_dir / "numerics" / "custom_observables.py").read_bytes() == before_custom
    attempt_match = re.search(r"attempt_id=([A-Za-z0-9_.-]+)", failed.stdout)
    assert attempt_match is not None

    second = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
            "--resume-attempt",
            attempt_match.group(1),
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


def test_init_analysis_default_preserves_existing_draft_and_allocates_new_id(
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
    first_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    first_payload = json.loads(first_path.read_text(encoding="utf-8"))
    first_payload["seed"] = 314159
    first_path.write_text(json.dumps(first_payload, indent=2) + "\n", encoding="utf-8")
    first_bytes = first_path.read_bytes()

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
    assert first_path.read_bytes() == first_bytes
    assert (
        project_dir / "numerics" / "scan-configs" / "analysis-002.json"
    ).is_file()


def test_init_analysis_formula_fallback_requires_explicit_opt_in(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)

    safe_default = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert safe_default.returncode == 0, safe_default.stdout + safe_default.stderr
    safe_config = json.loads(
        (
            project_dir / "numerics" / "scan-configs" / "analysis-001.json"
        ).read_text(encoding="utf-8")
    )
    assert safe_config["allow_formula_fallback"] is False
    assert "allow_formula_fallback=false" in safe_default.stdout

    opted_in = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--allow-formula-fallback",
        ],
        capture_output=True,
        text=True,
    )
    assert opted_in.returncode == 0, opted_in.stdout + opted_in.stderr
    opted_config = json.loads(
        (
            project_dir / "numerics" / "scan-configs" / "analysis-002.json"
        ).read_text(encoding="utf-8")
    )
    assert opted_config["allow_formula_fallback"] is True
    assert "allow_formula_fallback=true (explicit CLI opt-in)" in opted_in.stdout


def test_formula_only_initialization_creates_no_fake_observable_or_module(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.unlink()
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["constraints"] = [
        constraint
        for constraint in constraints["constraints"]
        if constraint["id"] in {"c-005", "c-006"}
    ]
    constraints_path.write_text(
        json.dumps(constraints, indent=2) + "\n",
        encoding="utf-8",
    )

    initialized = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    config = json.loads(
        (
            project_dir / "numerics" / "scan-configs" / "analysis-001.json"
        ).read_text(encoding="utf-8")
    )
    assert config["observables"] == []
    assert not custom_path.exists()
    assert "No custom-observable module is required" in initialized.stdout


def test_committed_analysis_init_cleanup_warning_is_success_without_retry(
    repo_root,
    tmp_path,
    monkeypatch,
    capsys,
    project_copy_factory,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    initializer = load_init_analysis_module(repo_root)
    original_commit = initializer.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise initializer.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        initializer.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(initializer.__file__),
            "--project-dir",
            str(project_dir),
        ],
    )

    assert initializer.main() == 0
    warning = capsys.readouterr().err
    assert "committed successfully" in warning
    assert "Do not retry" in warning
    assert "injected cleanup interruption" in warning
    assert (
        project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    ).is_file()
    assert not (
        project_dir
        / "numerics"
        / "scan-configs"
        / ".reservations"
        / "analysis-001"
    ).exists()


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


def test_init_analysis_writes_canonical_custom_observable_stub(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.unlink()

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
    source = custom_path.read_text(encoding="utf-8")
    assert source.count("def m_eff_bb(") == 1
    assert "Auto-generated observable stub for constraint c-003" in source
    assert "Original formula that could not be parsed safely:" in source
    assert "m_eff_bb compare with synthetic bound" in source
    signature = source.split("def m_eff_bb(", 1)[1].split(") -> float:", 1)[0]
    assert "M_Hpp: float," in signature
    assert "v_Delta: float," in signature
    assert "m_lightest: float," in signature
    assert "task_outputs:" not in signature

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
    assert custom_path.read_text(encoding="utf-8").count("def m_eff_bb(") == 1


def test_concurrent_initializers_claim_unique_analysis_ids(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    gate = tmp_path / "release-reservations"
    env = os.environ.copy()
    env["HEP_WORKFLOW_TEST_ANALYSIS_RESERVATION_GATE"] = str(gate)

    def start_worker(_: int) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                sys.executable,
                str(init_analysis_script),
                "--project-dir",
                str(project_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        processes = list(pool.map(start_worker, range(8)))

    reservation_root = project_dir / "numerics" / "scan-configs" / ".reservations"
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        reservations = [
            path
            for path in reservation_root.glob("analysis-*")
            if (path / "reservation.json").is_file()
        ]
        if len(reservations) == 8:
            break
        time.sleep(0.02)
    else:
        outputs = [process.communicate(timeout=2) for process in processes]
        raise AssertionError(f"workers did not reserve eight IDs: {outputs}")

    gate.touch()
    completed = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        completed.append((process.returncode, stdout, stderr))
    assert all(code == 0 for code, _, _ in completed), completed

    config_paths = sorted(
        (project_dir / "numerics" / "scan-configs").glob("analysis-*.json")
    )
    assert [path.stem for path in config_paths] == [
        f"analysis-{index:03d}" for index in range(1, 9)
    ]
    assert {
        json.loads(path.read_text(encoding="utf-8"))["analysis_id"]
        for path in config_paths
    } == {path.stem for path in config_paths}
    custom_source = (project_dir / "numerics" / "custom_observables.py").read_text(
        encoding="utf-8"
    )
    assert custom_source.count("def m_eff_bb(") == 1


def test_staggered_initializers_allocate_new_ids_without_aliasing(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)

    def run_worker(index: int) -> subprocess.CompletedProcess[str]:
        time.sleep(index * 0.01)
        return subprocess.run(
            [
                sys.executable,
                str(init_analysis_script),
                "--project-dir",
                str(project_dir),
            ],
            capture_output=True,
            text=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        completed = list(pool.map(run_worker, range(8)))

    assert all(item.returncode == 0 for item in completed), [
        (item.returncode, item.stdout, item.stderr) for item in completed
    ]
    configs = sorted(
        (project_dir / "numerics" / "scan-configs").glob("analysis-*.json")
    )
    assert [path.stem for path in configs] == [
        f"analysis-{index:03d}" for index in range(1, 9)
    ]


def test_metadata_less_abandoned_reservation_occupies_only_its_id(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    abandoned = (
        project_dir
        / "numerics"
        / "scan-configs"
        / ".reservations"
        / "analysis-777"
    )
    abandoned.mkdir(parents=True)

    completed = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (project_dir / "numerics" / "scan-configs" / "analysis-001.json").is_file()
    assert abandoned.is_dir()


def test_opaque_reservation_is_not_reclaimed_from_older_history(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    reservations = (
        project_dir / "numerics" / "scan-configs" / ".reservations"
    )
    opaque = reservations / "analysis-777"
    opaque.mkdir(parents=True)
    history = reservations / ".history"
    history.mkdir()
    history_entry = history / f"analysis-777-{'a' * 32}.json"
    history_entry.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "hep-numerics-analysis-init",
                "resource_id": "analysis-777",
                "attempt_id": "a" * 32,
                "state": "published",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    before = history_entry.read_bytes()

    collision = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-777",
        ],
        capture_output=True,
        text=True,
    )

    assert collision.returncode != 0
    assert "already occupied" in collision.stderr
    assert opaque.is_dir()
    assert list(opaque.iterdir()) == []
    assert history_entry.read_bytes() == before


def test_symlinked_reservation_is_rejected_without_touching_external_owner(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    reservations = (
        project_dir / "numerics" / "scan-configs" / ".reservations"
    )
    reservations.mkdir(parents=True)
    outside = tmp_path / "external-reservation-owner"
    outside.mkdir()
    sentinel = outside / "reservation.json"
    sentinel.write_text('{"external": true}\n', encoding="utf-8")
    before = sentinel.read_bytes()
    (reservations / "analysis-777").symlink_to(outside, target_is_directory=True)

    result = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must be a real directory" in result.stderr
    assert sentinel.read_bytes() == before
    assert (reservations / "analysis-777").is_symlink()
    assert not (project_dir / "numerics" / "scan-configs" / "analysis-001.json").exists()


def test_explicit_analysis_id_collision_fails_without_touching_owner(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    gate = tmp_path / "explicit-owner-gate"
    owner_env = os.environ.copy()
    owner_env["HEP_WORKFLOW_TEST_ANALYSIS_RESERVATION_GATE"] = str(gate)
    owner = subprocess.Popen(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-007",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=owner_env,
    )
    reservation = (
        project_dir
        / "numerics"
        / "scan-configs"
        / ".reservations"
        / "analysis-007"
        / "reservation.json"
    )
    deadline = time.monotonic() + 10.0
    while not reservation.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reservation.is_file()
    owner_metadata = reservation.read_bytes()

    collision = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-007",
        ],
        capture_output=True,
        text=True,
    )
    assert collision.returncode != 0
    assert "already occupied" in collision.stderr
    assert reservation.read_bytes() == owner_metadata

    gate.touch()
    owner_stdout, owner_stderr = owner.communicate(timeout=30)
    assert owner.returncode == 0, owner_stdout + owner_stderr
    assert (
        project_dir / "numerics" / "scan-configs" / "analysis-007.json"
    ).is_file()


def test_new_analysis_failure_rolls_back_config_and_custom_then_resumes(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.unlink()
    failed_env = os.environ.copy()
    failed_env["HEP_WORKFLOW_TEST_FAIL_ANALYSIS_INIT_AFTER"] = "analysis-011.json"
    failed = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-011",
        ],
        capture_output=True,
        text=True,
        env=failed_env,
    )
    assert failed.returncode != 0
    assert not custom_path.exists()
    assert not (
        project_dir / "numerics" / "scan-configs" / "analysis-011.json"
    ).exists()
    attempt_match = re.search(r"attempt_id=([A-Za-z0-9_.-]+)", failed.stdout)
    assert attempt_match is not None
    reservation_path = (
        project_dir
        / "numerics"
        / "scan-configs"
        / ".reservations"
        / "analysis-011"
        / "reservation.json"
    )
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    assert reservation["state"] == "failed"

    wrong = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-011",
            "--resume-attempt",
            "wrong-owner",
        ],
        capture_output=True,
        text=True,
    )
    assert wrong.returncode != 0
    assert not custom_path.exists()

    resumed = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-011",
            "--resume-attempt",
            attempt_match.group(1),
        ],
        capture_output=True,
        text=True,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert custom_path.is_file()
    assert (
        project_dir / "numerics" / "scan-configs" / "analysis-011.json"
    ).is_file()
    assert not reservation_path.exists()


def test_publishing_reservation_resumes_successfully_in_one_invocation(
    tmp_path,
    project_copy_factory,
    init_analysis_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reset_numerics_dirs(project_dir)
    failure_env = os.environ.copy()
    failure_env["HEP_WORKFLOW_TEST_FAIL_ANALYSIS_INIT_AFTER"] = "analysis-012.json"
    failed = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-012",
        ],
        capture_output=True,
        text=True,
        env=failure_env,
    )
    assert failed.returncode != 0
    attempt_match = re.search(r"attempt_id=([A-Za-z0-9_.-]+)", failed.stdout)
    assert attempt_match is not None
    reservation_path = (
        project_dir
        / "numerics"
        / "scan-configs"
        / ".reservations"
        / "analysis-012"
        / "reservation.json"
    )
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    reservation["state"] = "publishing"
    reservation_path.write_text(
        json.dumps(reservation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    resumed = subprocess.run(
        [
            sys.executable,
            str(init_analysis_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-012",
            "--resume-attempt",
            attempt_match.group(1),
        ],
        capture_output=True,
        text=True,
    )

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert (project_dir / "numerics" / "scan-configs" / "analysis-012.json").is_file()
    assert not reservation_path.exists()
