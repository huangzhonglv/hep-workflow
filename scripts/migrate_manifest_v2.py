#!/usr/bin/env python3
"""Check or explicitly migrate one workspace manifest to manifest_version 2."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

from _publication_transaction import (
    PublicationTransaction,
    TransactionCommittedCleanupError,
    capture_identity,
    publication_lock,
)


def load_manifest_helper(repo_root: Path) -> Any:
    helper_path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "_manifest.py"
    )
    spec = importlib.util.spec_from_file_location("hep_manifest_v2_migration", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load manifest helper from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_workspace_validator(repo_root: Path) -> Any:
    """Load the authoritative workspace validator for staged-candidate checks."""

    module_path = repo_root / "scripts" / "validate_workspace_projects.py"
    spec = importlib.util.spec_from_file_location(
        "manifest_v2_workspace_validator",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load workspace validator from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check a project manifest and optionally perform the explicit, "
            "fail-closed manifest v1 to v2 migration."
        )
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Publish the validated v2 candidate; without this flag the command is read-only.",
    )
    return parser.parse_args()


def validate_candidate(
    helper: Any,
    repo_root: Path,
    project_dir: Path,
    candidate: dict[str, Any],
) -> None:
    """Require the staged manifest and its complete workspace to validate."""

    from jsonschema import Draft202012Validator

    schema = helper.load_json(repo_root / "schemas" / "manifest.schema.json")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(candidate),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = "; ".join(
            f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"migrated candidate does not satisfy manifest v2: {rendered}")

    workspace_validator = load_workspace_validator(repo_root)
    validators = workspace_validator.load_schema_validators(repo_root)
    scan_config_validator = workspace_validator.load_validate_scan_config_module(repo_root)
    failures = workspace_validator.validate_project_snapshot(
        project_dir,
        validators,
        scan_config_validator,
        manifest_override=candidate,
    )
    if failures:
        raise ValueError(
            "migrated candidate failed authoritative workspace validation "
            f"with {failures} failing validation group(s)"
        )


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    project_dir = args.project_dir.resolve()
    manifest_path = project_dir / "manifest.json"
    try:
        helper = load_manifest_helper(repo_root)
        with publication_lock(
            project_dir,
            "manifest-v2-migration-check",
        ):
            manifest = helper.load_json(manifest_path)
            candidate = helper.migrate_manifest_v1(manifest, project_dir=project_dir)
            validate_candidate(helper, repo_root, project_dir, candidate)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if candidate == manifest:
        print(f"OK {manifest_path}: manifest_version=2; no migration needed")
        return 0
    if not args.write:
        print(
            f"NEEDS MIGRATION {manifest_path}: rerun with --write after reviewing the project"
        )
        return 1

    try:
        with publication_lock(
            project_dir,
            "manifest-v2-migration",
            blocking=True,
        ) as lock:
            # Re-read and rebuild under the writer lock. The earlier check is
            # advisory; this candidate is the one that is actually published.
            manifest = helper.load_json(manifest_path)
            candidate = helper.migrate_manifest_v1(
                manifest,
                project_dir=project_dir,
            )
            validate_candidate(helper, repo_root, project_dir, candidate)
            if candidate == manifest:
                print(f"OK {manifest_path}: manifest_version=2; no migration needed")
                return 0
            with PublicationTransaction.begin(
                project_dir,
                "manifest-v2-migration",
                lock=lock,
            ) as transaction:
                staged = transaction.stage_path("manifest.json")
                helper._write_staged_manifest_candidate(staged, candidate)
                transaction.add(
                    staged,
                    manifest_path,
                    mode="replace",
                    expected_before=capture_identity(manifest_path),
                )

                def verify_published_candidate() -> None:
                    if helper.load_json(manifest_path) != candidate:
                        raise ValueError("published manifest differs from candidate")

                transaction.commit(
                    post_publish_check=verify_published_candidate,
                )
    except TransactionCommittedCleanupError as exc:
        print(
            "warning: manifest migration committed successfully, but private cleanup "
            f"is pending for transaction {exc.transaction_id}: {exc.cleanup_error}. "
            "Do not retry this command; use recover_publication_transactions.py "
            "for the same publication anchor.",
            file=sys.stderr,
        )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"MIGRATED {manifest_path}: manifest_version=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
