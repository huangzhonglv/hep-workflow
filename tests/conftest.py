from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable

import pytest


def load_module_from_path(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def numerics_contract_fixture_path(repo_root: Path) -> Path:
    return (
        repo_root / "tests" / "fixtures" / "workspace-projects" / "numerics-contract"
    ).resolve()


@pytest.fixture(scope="session")
def smoke_e2e_fixture_path(repo_root: Path) -> Path:
    return (repo_root / "workspace" / "projects" / "smoke-e2e").resolve()


@pytest.fixture(scope="session")
def run_scan_module(repo_root: Path) -> Any:
    return load_module_from_path(
        "hep_numerics_run_scan_test_module",
        repo_root / ".agents" / "skills" / "hep-numerics" / "scripts" / "run_scan.py",
    )


@pytest.fixture(scope="session")
def run_scan_script(repo_root: Path) -> Path:
    return repo_root / ".agents" / "skills" / "hep-numerics" / "scripts" / "run_scan.py"


@pytest.fixture(scope="session")
def init_analysis_script(repo_root: Path) -> Path:
    return repo_root / ".agents" / "skills" / "hep-numerics" / "scripts" / "init_analysis.py"


@pytest.fixture(scope="session")
def make_figures_script(repo_root: Path) -> Path:
    return repo_root / ".agents" / "skills" / "hep-numerics" / "scripts" / "make_figures.py"


@pytest.fixture
def write_json() -> Callable[[Path, Any], None]:
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return _write_json


@pytest.fixture
def read_json() -> Callable[[Path], Any]:
    def _read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    return _read_json


@pytest.fixture
def project_copy_factory(numerics_contract_fixture_path: Path):
    created: list[Path] = []

    def _factory(
        tmp_path: Path,
        project_name: str = "numerics-contract",
        *,
        source_project_path: Path | None = None,
    ) -> Path:
        source = source_project_path or numerics_contract_fixture_path
        destination = tmp_path / "workspace" / "projects" / project_name
        shutil.copytree(source, destination)
        (destination / "calculations").mkdir(parents=True, exist_ok=True)
        (destination / "numerics" / "scan-configs").mkdir(parents=True, exist_ok=True)
        (destination / "numerics" / "scan-results").mkdir(parents=True, exist_ok=True)
        (destination / "numerics" / "figures").mkdir(parents=True, exist_ok=True)
        (destination / "literature" / "digitized").mkdir(parents=True, exist_ok=True)
        (destination / "literature" / "style").mkdir(parents=True, exist_ok=True)
        (destination / "reproduction" / "runs").mkdir(parents=True, exist_ok=True)
        (destination / "reproduction" / "figures").mkdir(parents=True, exist_ok=True)
        (destination / "reproduction" / "reports").mkdir(parents=True, exist_ok=True)
        created.append(destination)
        return destination

    yield _factory

    for destination in created:
        shutil.rmtree(destination / "numerics" / "scan-results", ignore_errors=True)
        shutil.rmtree(destination / "numerics" / "figures", ignore_errors=True)


@pytest.fixture
def ensure_task_result(write_json: Callable[[Path, Any], None], read_json: Callable[[Path], Any]):
    def _ensure_task_result(
        project_dir: Path,
        *,
        task_id: str = "task-001",
        observable: str = "Br_mu_to_egamma",
        function_name: str = "compute_observable",
        translation_status: str = "complete",
        python_body: str | None = None,
        parameter_specs: list[dict[str, Any]] | None = None,
    ) -> Path:
        task_dir = project_dir / "calculations" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        manifest = read_json(project_dir / "manifest.json")
        model_version = manifest["active_model_version"]
        model_checksum = manifest["artifacts"]["model"]["checksum"]
        calc_tasks = read_json(project_dir / "model" / "calc-tasks.json")
        task_type = next(
            (
                task.get("type")
                for task in calc_tasks.get("tasks", [])
                if task.get("task_id") == task_id
            ),
            "tree",
        )
        provenance = (
            "literature_formula_imported"
            if task_type == "loop"
            else "manual_tree_algebra"
        )

        if parameter_specs is None:
            parameter_specs = [
                {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
                {"canonical_name": "v_Delta", "role": "scan", "unit": "GeV"},
            ]

        python_source = python_body or textwrap.dedent(
            f"""
            from __future__ import annotations


            def {function_name}(*, M_Hpp: float, v_Delta: float = 1.0, **kwargs) -> float:
                safe_mass = max(float(M_Hpp), 1.0)
                safe_vev = max(float(v_Delta), 1.0e-12)
                return float(1.0e-13 * safe_vev * (100.0 / safe_mass) ** 2)
            """
        ).strip() + "\n"

        (task_dir / "request.md").write_text(
            "# Request\n\nMinimal stub task for hep-numerics tests.\n",
            encoding="utf-8",
        )
        (task_dir / "result-summary.md").write_text(
            "# Result Summary\n\n## Benchmark Verification\n\nNo benchmark in this test fixture.\n",
            encoding="utf-8",
        )
        (task_dir / "result.wl").write_text("(* test fixture stub *)\n", encoding="utf-8")
        (task_dir / "result-python.py").write_text(python_source, encoding="utf-8")
        (task_dir / "run-instructions.md").write_text(
            "# Run Instructions\n\nGenerated for pytest integration and contract tests.\n",
            encoding="utf-8",
        )

        write_json(
            task_dir / "result-meta.json",
            {
                "task_id": task_id,
                "observable": observable,
                "python_function": function_name,
                "python_file": "result-python.py",
                "parameters": parameter_specs,
                "return_value": {
                    "name": observable,
                    "unit": "dimensionless",
                    "description": f"Test fixture value for {observable}",
                },
                "translation_status": translation_status,
                "translation_notes": "Pytest-generated fixture.",
                "source_wl": "result.wl",
                "calculation_provenance": provenance,
                "benchmark_used_as_input": False,
                "package_x_methods": [],
                "provenance_notes": "Pytest-generated fixture, not a Package-X derivation.",
                "benchmark_status": "no_benchmark",
                "depends_on": {
                    "model_version": model_version,
                    "model_checksum": model_checksum,
                },
            },
        )

        return task_dir

    return _ensure_task_result


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e",
        action="store_true",
        help="run @pytest.mark.e2e tests (hep-numerics full workflow incl. wolframscript)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-e2e") or os.environ.get("HEP_E2E") == "1":
        return

    skip_e2e = pytest.mark.skip(
        reason="e2e tests disabled; pass --run-e2e or set HEP_E2E=1 to enable"
    )
    for item in items:
        if item.get_closest_marker("e2e"):
            item.add_marker(skip_e2e)
