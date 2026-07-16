"""Workflow-specific discovery for content-addressed dependency graphs.

The generic graph helper never guesses coverage.  This module derives the exact
project and repository files used by calculation, scan, and reproduction
workflows so producers and consumers can independently agree on that coverage.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

try:
    from _dependency_graph import (
        DependencySpec,
        make_spec,
        sha256_file,
        verify_dependency_graph,
    )
    from _strict_json import load_json
except ModuleNotFoundError:  # Imported as scripts._workflow_dependencies.
    from scripts._dependency_graph import (
        DependencySpec,
        make_spec,
        sha256_file,
        verify_dependency_graph,
    )
    from scripts._strict_json import load_json


_CALCULATION_MODEL_FILES = (
    ("model-spec", "model/model-spec.json"),
    ("calc-tasks", "model/calc-tasks.json"),
)

_CALCULATION_OPTIONAL_MODEL_FILES = (
    ("benchmarks", "model/benchmarks.json"),
)

_PACKAGE_SCRIBE_BOUND_FILES = (
    "SKILL.md",
    "examples/electroweak-minimal-examples.md",
    "examples/tutorial-examples.md",
    "references/custom-lagrangian-validation.md",
    "references/packagex-reference.md",
    "references/standard-theories.md",
    "scripts/check_package_result_placeholders.py",
    "scripts/finalize_package_result.py",
    "scripts/init_package_result_files.py",
    "scripts/_publication_transaction.py",
    "templates/request.md.tmpl",
    "templates/result-meta.json.tmpl",
    "templates/result-python.py.tmpl",
    "templates/result-summary.md.tmpl",
    "templates/run-instructions.md.tmpl",
)

_SCAN_REPOSITORY_FILES = (
    ("scan-config-schema", "schemas/scan-config.schema.json"),
    ("scan-meta-schema", "schemas/scan-meta.schema.json"),
    ("result-meta-schema", "schemas/result-meta.schema.json"),
    ("model-spec-schema", "schemas/model-spec.schema.json"),
    ("constraints-schema", "schemas/constraints-data.schema.json"),
)

_REPRODUCTION_REPOSITORY_FILES = (
    ("comparison-runner", "scripts/compare_to_reference.py"),
    ("comparison-metrics", "scripts/_compare_metrics.py"),
    ("comparison-figures", "scripts/_compare_figures.py"),
    ("calculation-provenance-validator", "scripts/_calculation_provenance.py"),
    ("reproduction-result-validator", "scripts/_reproduction_result_validation.py"),
    ("reproduction-readiness-validator", "scripts/_reproduction_readiness.py"),
    ("strict-json-helper", "scripts/_strict_json.py"),
    ("identity-helper", "scripts/_identity.py"),
    ("dependency-graph-helper", "scripts/_dependency_graph.py"),
    ("workflow-dependency-helper", "scripts/_workflow_dependencies.py"),
    ("scan-artifact-validator", "scripts/_scan_artifact_validation.py"),
    ("publication-transaction-helper", "scripts/_publication_transaction.py"),
    ("repro-targets-schema", "schemas/repro-targets.schema.json"),
    ("reproduction-result-schema", "schemas/reproduction-result.schema.json"),
    ("reproduction-readiness-schema", "schemas/reproduction-readiness.schema.json"),
    ("scan-config-schema", "schemas/scan-config.schema.json"),
    ("scan-meta-schema", "schemas/scan-meta.schema.json"),
    ("model-spec-schema", "schemas/model-spec.schema.json"),
    ("calc-tasks-schema", "schemas/calc-tasks.schema.json"),
    ("result-meta-schema", "schemas/result-meta.schema.json"),
    ("paper-extract-schema", "schemas/paper-extract.schema.json"),
    ("formula-reference-schema", "schemas/formula-reference.schema.json"),
    ("normalization-record-schema", "schemas/normalization-record.schema.json"),
    ("reproduction-readiness-contract", "docs/contracts/reproduction-readiness.md"),
)


def _project_spec(project_dir: Path, role: str, path: str | Path) -> DependencySpec:
    return make_spec("project", role, project_dir, path)


def _repository_spec(repo_root: Path, role: str, path: str | Path) -> DependencySpec:
    return make_spec("repository", role, repo_root, path)


def _deduplicate(specs: Iterable[DependencySpec]) -> list[DependencySpec]:
    by_key: dict[tuple[str, str, str], DependencySpec] = {}
    for spec in specs:
        key = (spec.scope, spec.path, spec.role)
        if key in by_key:
            raise ValueError(f"duplicate workflow dependency spec {key!r}")
        by_key[key] = spec
    return sorted(by_key.values(), key=lambda item: (item.scope, item.path, item.role))


def calculation_dependency_specs(
    project_dir: str | Path,
    repo_root: str | Path,
    task_id: str,
    result_meta: dict[str, Any] | None = None,
) -> list[DependencySpec]:
    """Return the files that a completed calculation result must bind."""

    project = Path(project_dir).resolve()
    repository = Path(repo_root).resolve()
    task_dir = project / "calculations" / task_id
    metadata = result_meta
    if metadata is None:
        loaded = load_json(task_dir / "result-meta.json")
        if not isinstance(loaded, dict):
            raise ValueError(f"{task_id} result-meta must be an object")
        metadata = loaded

    specs: list[DependencySpec] = [
        _project_spec(project, role, relpath)
        for role, relpath in _CALCULATION_MODEL_FILES
    ]
    for role, relpath in _CALCULATION_OPTIONAL_MODEL_FILES:
        if (project / relpath).is_file():
            specs.append(_project_spec(project, role, relpath))
    for role, filename in (
        (f"{task_id}-request", "request.md"),
        (f"{task_id}-result-summary", "result-summary.md"),
        (f"{task_id}-result-wl", str(metadata.get("source_wl", "result.wl"))),
        (f"{task_id}-result-python", str(metadata.get("python_file", "result-python.py"))),
    ):
        specs.append(_project_spec(project, role, task_dir / filename))

    for mirror_root in (".agents", ".claude"):
        mirror_role = mirror_root.removeprefix(".")
        for relpath in _PACKAGE_SCRIBE_BOUND_FILES:
            role = "package-scribe-" + mirror_role + "-" + relpath.replace("/", "-")
            specs.append(
                _repository_spec(
                    repository,
                    role,
                    f"{mirror_root}/skills/package-scribe/{relpath}",
                )
            )

    for role, relpath in (
        ("result-meta-schema", "schemas/result-meta.schema.json"),
        ("calculation-provenance-validator", "scripts/_calculation_provenance.py"),
        ("dependency-graph-helper", "scripts/_dependency_graph.py"),
        ("workflow-dependency-helper", "scripts/_workflow_dependencies.py"),
    ):
        specs.append(_repository_spec(repository, role, relpath))
    return _deduplicate(specs)


def scan_dependency_specs(
    project_dir: str | Path,
    repo_root: str | Path,
    scan_config_path: str | Path,
    scan_config: dict[str, Any],
    *,
    producer_script: str | Path,
) -> list[DependencySpec]:
    """Return the exact data/code inputs used by a hep-numerics scan."""

    project = Path(project_dir).resolve()
    repository = Path(repo_root).resolve()
    specs: list[DependencySpec] = [
        _project_spec(project, "scan-config", scan_config_path),
        *(
            _project_spec(project, role, relpath)
            for role, relpath in _CALCULATION_MODEL_FILES
        ),
        _project_spec(project, "constraints-data", "constraints/constraints-data.json"),
    ]

    for task_id in scan_config.get("depends_on", {}).get("task_ids", []):
        task_dir = project / "calculations" / str(task_id)
        metadata = load_json(task_dir / "result-meta.json")
        if not isinstance(metadata, dict):
            raise ValueError(f"{task_id} result-meta must be an object")
        specs.extend(
            (
                _project_spec(project, f"{task_id}-result-meta", task_dir / "result-meta.json"),
                _project_spec(
                    project,
                    f"{task_id}-result-python",
                    task_dir / str(metadata.get("python_file", "result-python.py")),
                ),
                _project_spec(
                    project,
                    f"{task_id}-result-wl",
                    task_dir / str(metadata.get("source_wl", "result.wl")),
                ),
            )
        )

    if any(
        binding.get("source", {}).get("type") == "custom"
        for binding in scan_config.get("observables", [])
        if isinstance(binding, dict)
    ):
        specs.append(
            _project_spec(
                project,
                "custom-observables",
                "numerics/custom_observables.py",
            )
        )

    constraints_payload = load_json(project / "constraints" / "constraints-data.json")
    constraints = {
        str(item.get("id")): item
        for item in constraints_payload.get("constraints", [])
        if isinstance(item, dict)
    }
    for constraint_id in scan_config.get("constraints_used", []):
        constraint = constraints.get(str(constraint_id), {})
        interpolation = constraint.get("interpolation")
        if isinstance(interpolation, dict) and isinstance(interpolation.get("file"), str):
            specs.append(
                _project_spec(
                    project,
                    f"{constraint_id}-interpolation-table",
                    interpolation["file"],
                )
            )

    producer = Path(producer_script).resolve()
    specs.append(_repository_spec(repository, "scan-runner", producer))
    producer_dir = producer.parent
    for role, filename in (
        ("manifest-helper", "_manifest.py"),
        ("custom-observables-helper", "_custom_observables.py"),
        ("strict-json-helper", "_strict_json.py"),
        ("identity-helper", "_identity.py"),
        ("dependency-graph-helper", "_dependency_graph.py"),
        ("workflow-dependency-helper", "_workflow_dependencies.py"),
        ("scan-artifact-validator", "_scan_artifact_validation.py"),
        ("publication-transaction-helper", "_publication_transaction.py"),
    ):
        specs.append(_repository_spec(repository, role, producer_dir / filename))
    specs.extend(
        _repository_spec(repository, role, relpath)
        for role, relpath in _SCAN_REPOSITORY_FILES
    )
    return _deduplicate(specs)


def verify_frozen_scan_dependency_graph(
    graph: object,
    project_dir: str | Path,
    repo_root: str | Path,
    expected_specs: Iterable[DependencySpec],
    *,
    scan_config_source: str,
    required_roles: Iterable[str] | None = None,
) -> list[str]:
    """Verify a scan graph against its embedded exact config and current other inputs.

    The live scan-config may differ only in renderer fields.  Its execution
    semantics are checked separately against ``scan_config_snapshot``; this
    function proves that the graph's recorded scan-config hash belongs to the
    embedded exact source while every other graph entry still matches disk.
    """

    specs = list(expected_specs)
    issues = verify_dependency_graph(
        graph,
        project_dir,
        repo_root,
        expected_specs=specs,
        required_roles=required_roles,
        check_current_bytes=False,
    )
    if issues:
        return issues
    if not isinstance(graph, dict) or not isinstance(graph.get("entries"), list):
        return ["scan dependency graph entries are unavailable"]

    recorded = {
        (entry["scope"], entry["path"], entry["role"]): entry["sha256"]
        for entry in graph["entries"]
        if isinstance(entry, dict)
        and set(entry) == {"scope", "path", "role", "sha256"}
    }
    project = Path(project_dir).resolve()
    repository = Path(repo_root).resolve()
    frozen_hash = "sha256:" + hashlib.sha256(
        scan_config_source.encode("utf-8")
    ).hexdigest()
    scan_config_count = 0
    for spec in specs:
        key = (spec.scope, spec.path, spec.role)
        declared = recorded.get(key)
        if declared is None:
            continue
        if spec.role == "scan-config":
            scan_config_count += 1
            if declared != frozen_hash:
                issues.append(
                    "scan-config dependency hash does not match embedded exact source"
                )
            continue
        root = project if spec.scope == "project" else repository
        try:
            actual = sha256_file(root / spec.path)
        except ValueError as exc:
            issues.append(f"cannot hash current {spec.scope}:{spec.path}: {exc}")
            continue
        if declared != actual:
            issues.append(
                "dependency hash does not match current exact bytes for "
                f"{spec.scope}:{spec.path}"
            )
    if scan_config_count != 1:
        issues.append(
            "scan dependency coverage must contain exactly one scan-config role"
        )
    return issues


def figure_dependency_specs(
    project_dir: str | Path,
    repo_root: str | Path,
    *,
    scan_config_path: str | Path,
    scan_csv_path: str | Path,
    scan_meta_path: str | Path,
    renderer_script: str | Path,
) -> list[DependencySpec]:
    """Return exact live inputs used to render one figure generation."""

    project = Path(project_dir).resolve()
    repository = Path(repo_root).resolve()
    renderer = Path(renderer_script).resolve()
    specs = [
        _project_spec(project, "figure-scan-config", scan_config_path),
        _project_spec(project, "figure-scan-csv", scan_csv_path),
        _project_spec(project, "figure-scan-meta", scan_meta_path),
        _project_spec(project, "figure-model-spec", "model/model-spec.json"),
        _project_spec(
            project,
            "figure-constraints-data",
            "constraints/constraints-data.json",
        ),
        _repository_spec(repository, "figure-renderer", renderer),
    ]
    renderer_dir = renderer.parent
    for role, filename in (
        ("figure-summary-writer", "run_scan.py"),
        ("figure-manifest-helper", "_manifest.py"),
        ("figure-strict-json-helper", "_strict_json.py"),
        ("figure-identity-helper", "_identity.py"),
        ("figure-dependency-graph-helper", "_dependency_graph.py"),
        ("figure-workflow-dependency-helper", "_workflow_dependencies.py"),
        ("figure-scan-artifact-validator", "_scan_artifact_validation.py"),
        ("figure-publication-transaction-helper", "_publication_transaction.py"),
    ):
        specs.append(_repository_spec(repository, role, renderer_dir / filename))
    for role, relpath in (
        ("figure-meta-schema", "schemas/figure-meta.schema.json"),
        ("figure-scan-config-schema", "schemas/scan-config.schema.json"),
        ("figure-scan-meta-schema", "schemas/scan-meta.schema.json"),
        ("figure-manifest-schema", "schemas/manifest.schema.json"),
    ):
        specs.append(_repository_spec(repository, role, relpath))
    return _deduplicate(specs)


def figure_producer_from_graph(graph: dict[str, Any], repo_root: str | Path) -> Path:
    """Resolve and allowlist the renderer recorded by figure provenance."""

    matches = [
        entry
        for entry in graph.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("scope") == "repository"
        and entry.get("role") == "figure-renderer"
    ]
    if len(matches) != 1:
        raise ValueError("figure dependency graph must contain one figure-renderer entry")
    relative = matches[0].get("path")
    allowed = {
        ".agents/skills/hep-numerics/scripts/make_figures.py",
        ".claude/skills/hep-numerics/scripts/make_figures.py",
    }
    if relative not in allowed:
        raise ValueError(f"unrecognized figure-renderer dependency path: {relative!r}")
    return Path(repo_root).resolve() / str(relative)


def scan_producer_from_graph(graph: dict[str, Any], repo_root: str | Path) -> Path:
    """Resolve and allowlist the runner recorded by a scan graph."""

    matches = [
        entry
        for entry in graph.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("scope") == "repository"
        and entry.get("role") == "scan-runner"
    ]
    if len(matches) != 1:
        raise ValueError("scan dependency graph must contain exactly one scan-runner entry")
    relative = matches[0].get("path")
    allowed = {
        ".agents/skills/hep-numerics/scripts/run_scan.py",
        ".claude/skills/hep-numerics/scripts/run_scan.py",
    }
    if relative not in allowed:
        raise ValueError(f"unrecognized scan-runner dependency path: {relative!r}")
    return Path(repo_root).resolve() / str(relative)


def reproduction_scan_required_target_ids(
    targets: Iterable[dict[str, Any]],
    paper_extract: dict[str, Any],
) -> set[str]:
    """Return selected quantitative targets whose declared hints permit a scan.

    A non-formula target is orchestrator-blocked when its paper-extract scan hint
    is absent or declares missing fields.  Keeping this mechanical decision here
    lets the producer and persisted-result validator derive scan coverage from
    the same authoritative project inputs instead of trusting output warnings.
    """

    raw_hints = paper_extract.get("scan_config_hints")
    if not isinstance(raw_hints, list):
        raise ValueError("paper-extract.scan_config_hints must be an array")
    hints: dict[str, dict[str, Any]] = {}
    for index, raw_hint in enumerate(raw_hints):
        if not isinstance(raw_hint, dict):
            raise ValueError(f"paper-extract scan hint {index} must be an object")
        target_id = raw_hint.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError(
                f"paper-extract scan hint {index} requires a nonempty target_id"
            )
        if target_id in hints:
            raise ValueError(
                f"paper-extract contains duplicate scan hints for {target_id!r}"
            )
        missing_fields = raw_hint.get("missing_fields")
        if not isinstance(missing_fields, list):
            raise ValueError(
                f"paper-extract scan hint {target_id!r} missing_fields must be an array"
            )
        hints[target_id] = raw_hint

    required: set[str] = set()
    for target in targets:
        if not isinstance(target, dict) or target.get("kind") == "formula":
            continue
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("selected reproduction target requires a nonempty id")
        hint = hints.get(target_id)
        if hint is not None and not hint["missing_fields"]:
            required.add(target_id)
    return required


def reproduction_dependency_specs(
    project_dir: str | Path,
    repo_root: str | Path,
    targets: Iterable[dict[str, Any]],
    task_ids: Iterable[str],
    *,
    analysis_id: str,
    include_scan: bool,
) -> list[DependencySpec]:
    """Return the files consumed by one reproduction comparison run."""

    project = Path(project_dir).resolve()
    repository = Path(repo_root).resolve()
    target_list = list(targets)
    uses_computation = any(
        target.get("kind") != "formula"
        for target in target_list
        if isinstance(target, dict)
    )
    specs: list[DependencySpec] = [
        _project_spec(project, "paper-extract", "literature/paper-extract.json"),
        _project_spec(project, "repro-targets", "literature/repro-targets.json"),
    ]
    if uses_computation:
        specs.extend(
            (
                *(
                    _project_spec(project, role, relpath)
                    for role, relpath in _CALCULATION_MODEL_FILES
                ),
                _project_spec(
                    project,
                    "constraints-data",
                    "constraints/constraints-data.json",
                ),
            )
        )
    style_path = project / "literature" / "style" / "paper-style.mplstyle"
    if style_path.exists():
        specs.append(_project_spec(project, "paper-style", style_path))

    for task_id in sorted(set(task_ids)):
        task_dir = project / "calculations" / task_id
        result_meta_path = task_dir / "result-meta.json"
        if not result_meta_path.exists():
            continue
        metadata = load_json(result_meta_path)
        if not isinstance(metadata, dict):
            raise ValueError(f"{task_id} result-meta must be an object")
        specs.extend(
            (
                _project_spec(project, f"{task_id}-result-meta", result_meta_path),
                _project_spec(
                    project,
                    f"{task_id}-result-python",
                    task_dir / str(metadata.get("python_file", "result-python.py")),
                ),
                _project_spec(
                    project,
                    f"{task_id}-result-wl",
                    task_dir / str(metadata.get("source_wl", "result.wl")),
                ),
            )
        )

    for target in target_list:
        target_id = str(target.get("id", "target"))
        normalization = target.get("normalization")
        candidate_paths = [("reference-data", target.get("data_file"))]
        if isinstance(normalization, dict):
            candidate_paths.extend(
                (
                    ("reference-raw", normalization.get("source_data_file")),
                    ("normalization-record", normalization.get("record_file")),
                )
            )
        for suffix, relpath in candidate_paths:
            if isinstance(relpath, str) and relpath:
                specs.append(
                    _project_spec(project, f"{target_id}-{suffix}", relpath)
                )

    if include_scan:
        result_root = Path("numerics") / "scan-results" / analysis_id
        specs.extend(
            (
                _project_spec(
                    project,
                    "scan-config",
                    Path("numerics") / "scan-configs" / f"{analysis_id}.json",
                ),
                _project_spec(project, "scan-meta", result_root / "scan.meta.json"),
                _project_spec(project, "scan-csv", result_root / "scan.csv"),
            )
        )

    specs.extend(
        _repository_spec(repository, role, relpath)
        for role, relpath in _REPRODUCTION_REPOSITORY_FILES
    )
    return _deduplicate(specs)
