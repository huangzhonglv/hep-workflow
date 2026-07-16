from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts._dependency_graph import (
    GRAPH_VERSION,
    DependencySpec,
    build_dependency_graph,
    make_spec,
    sha256_file,
    verify_dependency_graph,
)
from scripts._workflow_dependencies import calculation_dependency_specs


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    project_root = tmp_path / "project"
    repo_root.mkdir()
    project_root.mkdir()
    return project_root, repo_root


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_dependency_graph_schema_matches_verified_and_legacy_contract(
    repo_root: Path,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "dependency-graph.schema.json").read_text(
            encoding="utf-8"
        )
    )
    example = json.loads(
        (
            repo_root
            / "schemas"
            / "examples"
            / "dependency-graph.example.json"
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)

    assert list(validator.iter_errors(example)) == []
    root_payload = {
        "entries": example["entries"],
        "verification_status": "verified",
        "version": GRAPH_VERSION,
    }
    root_bytes = json.dumps(
        root_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert example["root_sha256"] == (
        "sha256:" + hashlib.sha256(root_bytes).hexdigest()
    )
    legacy = {
        "version": GRAPH_VERSION,
        "verification_status": "legacy-unverified",
        "reason": "Historical artifact predates dependency graphs.",
    }
    assert list(validator.iter_errors(legacy)) == []

    unsafe = json.loads(json.dumps(example))
    unsafe["entries"][0]["path"] = "../model-spec.json"
    assert list(validator.iter_errors(unsafe))
    unsafe_nul = json.loads(json.dumps(example))
    unsafe_nul["entries"][0]["path"] = "a\x00b"
    assert list(validator.iter_errors(unsafe_nul))
    legacy["entries"] = []
    assert list(validator.iter_errors(legacy))


@pytest.mark.parametrize(
    "example_name",
    [
        "result-meta.example.json",
        "scan-meta.example.json",
        "reproduction-result.example.json",
    ],
)
def test_embedded_example_graph_roots_match_their_entries(
    repo_root: Path,
    example_name: str,
) -> None:
    example = json.loads(
        (repo_root / "schemas" / "examples" / example_name).read_text(
            encoding="utf-8"
        )
    )
    graph = example["input_provenance"]
    canonical = json.dumps(
        {
            "entries": sorted(
                graph["entries"],
                key=lambda entry: (
                    entry["scope"],
                    entry["path"],
                    entry["role"],
                ),
            ),
            "verification_status": "verified",
            "version": GRAPH_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    assert graph["root_sha256"] == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )


def test_sha256_file_hashes_exact_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path / "value.txt", b"one\r\ntwo\n")
    expected = "sha256:" + hashlib.sha256(b"one\r\ntwo\n").hexdigest()
    assert sha256_file(path) == expected

    path.write_bytes(b"one\ntwo\n")
    assert sha256_file(path) != expected


def test_build_rejects_empty_verified_graph(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    with pytest.raises(ValueError, match="at least one dependency spec"):
        build_dependency_graph(project_root, repo_root, [])


def test_build_graph_is_sorted_and_root_is_canonical(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    project_file = _write(project_root / "model" / "μ.json", b"{}\n")
    repo_file = _write(repo_root / "scripts" / "runner.py", b"pass\n")
    project_spec = make_spec("project", "model_spec", project_root, project_file)
    repo_spec = make_spec("repository", "runner", repo_root, "scripts/runner.py")

    first = build_dependency_graph(project_root, repo_root, [repo_spec, project_spec])
    second = build_dependency_graph(project_root, repo_root, [project_spec, repo_spec])

    assert first == second
    assert first["version"] == GRAPH_VERSION
    assert [entry["scope"] for entry in first["entries"]] == [
        "project",
        "repository",
    ]
    payload = {
        "entries": first["entries"],
        "verification_status": "verified",
        "version": GRAPH_VERSION,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert first["root_sha256"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert verify_dependency_graph(first, project_root, repo_root) == []


def test_one_byte_mutation_is_detected(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    path = _write(project_root / "model.json", b'{"x":1}\n')
    spec = make_spec("project", "model", project_root, path)
    graph = build_dependency_graph(project_root, repo_root, [spec])

    path.write_bytes(b'{"x":2}\n')
    errors = verify_dependency_graph(graph, project_root, repo_root)

    assert any("does not match current exact bytes" in error for error in errors)


def test_historical_verification_skips_only_current_byte_comparison(
    tmp_path: Path,
) -> None:
    project_root, repo_root = _roots(tmp_path)
    first_path = _write(project_root / "first.txt", b"first")
    second_path = _write(project_root / "second.txt", b"second")
    first = make_spec("project", "first", project_root, first_path)
    second = make_spec("project", "second", project_root, second_path)
    graph = build_dependency_graph(project_root, repo_root, [first])

    first_path.write_bytes(b"changed")
    assert any(
        "does not match current exact bytes" in error
        for error in verify_dependency_graph(graph, project_root, repo_root)
    )
    assert (
        verify_dependency_graph(
            graph,
            project_root,
            repo_root,
            expected_specs=[first],
            check_current_bytes=False,
        )
        == []
    )

    incomplete = verify_dependency_graph(
        graph,
        project_root,
        repo_root,
        expected_specs=[second],
        check_current_bytes=False,
    )
    assert any("missing expected entries" in error for error in incomplete)
    assert any("unexpected entries" in error for error in incomplete)

    invalid_root = json.loads(json.dumps(graph))
    invalid_root["root_sha256"] = "sha256:" + "0" * 64
    assert any(
        "root_sha256 does not match" in error
        for error in verify_dependency_graph(
            invalid_root,
            project_root,
            repo_root,
            check_current_bytes=False,
        )
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        "../outside",
        "a/../b",
        "a/./b",
        "a//b",
        "a\\b",
        "a\x00b",
        "/absolute",
        "C:/absolute",
    ],
)
def test_make_spec_rejects_unsafe_paths(tmp_path: Path, unsafe: str) -> None:
    project_root, _ = _roots(tmp_path)
    with pytest.raises(ValueError, match="dependency path|outside"):
        make_spec("project", "input", project_root, unsafe)


def test_make_spec_rejects_symlink_components(tmp_path: Path) -> None:
    project_root, _ = _roots(tmp_path)
    real_dir = project_root / "real"
    real_file = _write(real_dir / "input.txt", b"value")
    (project_root / "alias").symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink component"):
        make_spec("project", "input", project_root, "alias/input.txt")
    with pytest.raises(ValueError, match="symlink component"):
        make_spec(
            "project",
            "input",
            project_root,
            project_root / "alias" / "input.txt",
        )

    assert make_spec("project", "input", project_root, real_file).path == "real/input.txt"


def test_make_spec_accepts_macos_var_root_alias_without_losing_suffix_checks(
    tmp_path: Path,
) -> None:
    private_var = Path("/private/var")
    var = Path("/var")
    if not private_var.is_dir() or var.resolve() != private_var.resolve():
        pytest.skip("macOS /var -> /private/var alias is unavailable")

    project_root, _ = _roots(tmp_path)
    resolved_root = project_root.resolve()
    try:
        root_suffix = resolved_root.relative_to(private_var)
    except ValueError:
        pytest.skip("pytest temporary directory is not under /private/var")

    real_file = _write(project_root / "real" / "input.txt", b"value")
    alias_root = var / root_suffix
    alias_file = alias_root / "real" / "input.txt"
    assert make_spec("project", "input", project_root, alias_file).path == (
        "real/input.txt"
    )

    (project_root / "link").symlink_to(real_file.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink component"):
        make_spec(
            "project",
            "input",
            project_root,
            alias_root / "link" / "input.txt",
        )


def test_nonregular_dependency_is_rejected(tmp_path: Path) -> None:
    project_root, _ = _roots(tmp_path)
    (project_root / "directory").mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        make_spec("project", "input", project_root, "directory")


def test_duplicate_key_and_hardlink_alias_are_rejected(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    first_path = _write(project_root / "first.txt", b"same inode")
    first = make_spec("project", "input", project_root, first_path)
    with pytest.raises(ValueError, match="duplicate dependency key"):
        build_dependency_graph(project_root, repo_root, [first, first])

    second_path = project_root / "second.txt"
    os.link(first_path, second_path)
    second = make_spec("project", "other", project_root, second_path)
    with pytest.raises(ValueError, match="alias the same filesystem object"):
        build_dependency_graph(project_root, repo_root, [first, second])


def test_expected_specs_require_exact_coverage(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    first_path = _write(project_root / "first.txt", b"first")
    second_path = _write(project_root / "second.txt", b"second")
    first = make_spec("project", "first", project_root, first_path)
    second = make_spec("project", "second", project_root, second_path)
    graph = build_dependency_graph(project_root, repo_root, [first])

    errors = verify_dependency_graph(
        graph,
        project_root,
        repo_root,
        expected_specs=[second],
    )

    assert any("missing expected entries" in error for error in errors)
    assert any("unexpected entries" in error for error in errors)


def test_calculation_graph_requires_shared_provenance_validator(
    repo_root: Path,
) -> None:
    project_root = repo_root / "workspace" / "projects" / "smoke-e2e"
    specs = calculation_dependency_specs(
        project_root,
        repo_root,
        "task-001",
    )
    provenance_specs = [
        spec
        for spec in specs
        if spec.role == "calculation-provenance-validator"
    ]

    assert len(provenance_specs) == 1
    assert provenance_specs[0].scope == "repository"
    assert provenance_specs[0].path == "scripts/_calculation_provenance.py"

    incomplete = [
        spec
        for spec in specs
        if spec.role != "calculation-provenance-validator"
    ]
    graph = build_dependency_graph(project_root, repo_root, incomplete)
    errors = verify_dependency_graph(
        graph,
        project_root,
        repo_root,
        expected_specs=specs,
    )

    assert any(
        "calculation-provenance-validator" in error
        and "missing expected entries" in error
        for error in errors
    )


def test_required_roles_are_enforced(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    path = _write(project_root / "input.txt", b"input")
    graph = build_dependency_graph(
        project_root,
        repo_root,
        [make_spec("project", "model", project_root, path)],
    )

    errors = verify_dependency_graph(
        graph,
        project_root,
        repo_root,
        required_roles=["model", "runner"],
    )
    assert errors == ["dependency graph is missing required roles: ['runner']"]


def test_root_and_entry_order_tampering_are_rejected(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    one = make_spec(
        "project", "one", project_root, _write(project_root / "one", b"1")
    )
    two = make_spec(
        "project", "two", project_root, _write(project_root / "two", b"2")
    )
    graph = build_dependency_graph(project_root, repo_root, [one, two])

    wrong_root = json.loads(json.dumps(graph))
    wrong_root["root_sha256"] = "sha256:" + "0" * 64
    assert any(
        "root_sha256 does not match" in error
        for error in verify_dependency_graph(wrong_root, project_root, repo_root)
    )

    reversed_entries = json.loads(json.dumps(graph))
    reversed_entries["entries"].reverse()
    assert any(
        "not in canonical" in error
        for error in verify_dependency_graph(reversed_entries, project_root, repo_root)
    )


def test_verify_rejects_duplicate_keys_and_hardlink_aliases(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    first_path = _write(project_root / "first", b"first")
    first = make_spec("project", "first", project_root, first_path)
    graph = build_dependency_graph(project_root, repo_root, [first])

    duplicate = json.loads(json.dumps(graph))
    duplicate["entries"].append(dict(duplicate["entries"][0]))
    assert any(
        "duplicate dependency key" in error
        for error in verify_dependency_graph(duplicate, project_root, repo_root)
    )

    alias_path = project_root / "alias"
    os.link(first_path, alias_path)
    alias = {
        "scope": "project",
        "role": "alias",
        "path": "alias",
        "sha256": sha256_file(alias_path),
    }
    aliased = build_dependency_graph(project_root, repo_root, [first])
    aliased["entries"].append(alias)
    aliased["entries"].sort(key=lambda item: (item["scope"], item["path"], item["role"]))
    payload = {
        "entries": aliased["entries"],
        "verification_status": "verified",
        "version": GRAPH_VERSION,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    aliased["root_sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert any(
        "alias the same filesystem object" in error
        for error in verify_dependency_graph(aliased, project_root, repo_root)
    )


def test_legacy_graph_requires_explicit_allowance(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    legacy = {
        "version": GRAPH_VERSION,
        "verification_status": "legacy-unverified",
        "reason": "Historical artifact predates dependency graphs.",
    }

    assert verify_dependency_graph(legacy, project_root, repo_root) == [
        "legacy-unverified dependency graph is not allowed"
    ]
    assert verify_dependency_graph(
        legacy,
        project_root,
        repo_root,
        allow_legacy=True,
    ) == []

    malformed = dict(legacy, entries=[])
    errors = verify_dependency_graph(
        malformed,
        project_root,
        repo_root,
        allow_legacy=True,
    )
    assert errors == [
        "legacy-unverified dependency graph must not contain entries or root_sha256"
    ]


def test_direct_invalid_specs_and_malformed_graph_fail_closed(tmp_path: Path) -> None:
    project_root, repo_root = _roots(tmp_path)
    _write(project_root / "input", b"input")
    invalid = DependencySpec("project", "input", "../input")
    with pytest.raises(ValueError, match="parent component"):
        build_dependency_graph(project_root, repo_root, [invalid])

    malformed = {
        "version": GRAPH_VERSION,
        "verification_status": "verified",
        "entries": [
            {
                "scope": "project",
                "role": "input",
                "path": "/absolute",
                "sha256": "sha256:" + "0" * 64,
            }
        ],
        "root_sha256": "sha256:" + "0" * 64,
    }
    errors = verify_dependency_graph(malformed, project_root, repo_root)
    assert any("must be relative" in error for error in errors)
