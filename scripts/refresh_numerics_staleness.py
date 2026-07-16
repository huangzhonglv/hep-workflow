#!/usr/bin/env python3
"""Check or transactionally publish the derived numerics stale projection."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from _publication_transaction import (
    PublicationTransaction,
    TransactionCommittedCleanupError,
    capture_identity,
    publication_lock,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check the manifest-v2 numerics stale projection and optionally "
            "publish the exact derived update transactionally."
        )
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Publish the derived candidate; without this flag the command is read-only.",
    )
    return parser.parse_args(argv)


def load_manifest_helper(repo_root: Path) -> Any:
    helper_path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "_manifest.py"
    )
    spec = importlib.util.spec_from_file_location(
        "hep_numerics_staleness_manifest",
        helper_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load manifest helper from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def build_candidate(
    helper: Any,
    manifest: dict[str, Any],
    *,
    project_dir: Path,
) -> dict[str, Any]:
    candidate = helper.refresh_numerics_staleness(
        manifest,
        project_dir=project_dir,
    )
    if candidate != manifest:
        candidate["last_updated"] = _utc_date()
    return candidate


def _manifest_schema_errors(
    helper: Any,
    repo_root: Path,
    candidate: dict[str, Any],
) -> list[str]:
    from jsonschema import Draft202012Validator

    schema = helper.load_json(repo_root / "schemas" / "manifest.schema.json")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(candidate),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in errors
    ]


def _schema_errors(
    helper: Any,
    repo_root: Path,
    payload: Any,
    schema_name: str,
) -> list[str]:
    from jsonschema import Draft202012Validator

    schema = helper.load_json(repo_root / "schemas" / schema_name)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in errors
    ]


def _validate_active_inputs(
    helper: Any,
    repo_root: Path,
    project_dir: Path,
    manifest: dict[str, Any],
) -> None:
    model_path = project_dir / "model" / "model-spec.json"
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    model_spec = helper.load_json(model_path)
    constraints_data = helper.load_json(constraints_path)
    schema_failures = [
        *(
            f"model/model-spec.json: {error}"
            for error in _schema_errors(
                helper,
                repo_root,
                model_spec,
                "model-spec.schema.json",
            )
        ),
        *(
            f"constraints/constraints-data.json: {error}"
            for error in _schema_errors(
                helper,
                repo_root,
                constraints_data,
                "constraints-data.schema.json",
            )
        ),
    ]
    if schema_failures:
        raise ValueError(
            "active model/constraints failed schema validation: "
            + "; ".join(schema_failures)
        )

    artifacts = manifest.get("artifacts")
    model = artifacts.get("model") if isinstance(artifacts, dict) else None
    constraints = (
        artifacts.get("constraints") if isinstance(artifacts, dict) else None
    )
    if not isinstance(model, dict) or not isinstance(constraints, dict):
        raise ValueError("manifest model and constraints artifacts must be objects")
    actual_model_checksum = helper.file_sha256(model_path)
    if model.get("checksum") != actual_model_checksum:
        raise ValueError(
            "manifest artifacts.model.checksum does not match model/model-spec.json"
        )
    if model.get("version") != model_spec.get("version"):
        raise ValueError(
            "manifest artifacts.model.version does not match model/model-spec.json"
        )
    if manifest.get("active_model_version") != model.get("version"):
        raise ValueError(
            "manifest active_model_version does not match artifacts.model.version"
        )
    declared_constraint_model_version = constraints_data.get("model_version")
    if (
        declared_constraint_model_version is not None
        and declared_constraint_model_version != model.get("version")
    ):
        raise ValueError(
            "constraints/constraints-data.json model_version does not match "
            "the active model version"
        )
    constraint_model = constraints.get("depends_on", {}).get("model")
    if not isinstance(constraint_model, dict) or (
        constraint_model.get("version") != model.get("version")
        or constraint_model.get("checksum") != model.get("checksum")
    ):
        raise ValueError(
            "manifest constraints dependency does not match the active model"
        )


def _validate_narrow_transition(
    before: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    for key, value in before.items():
        if key in {"last_updated", "artifacts"}:
            continue
        if candidate.get(key) != value:
            raise ValueError(f"staleness refresh changed unrelated manifest field {key!r}")

    before_artifacts = before.get("artifacts")
    candidate_artifacts = candidate.get("artifacts")
    if not isinstance(before_artifacts, dict) or not isinstance(candidate_artifacts, dict):
        raise ValueError("manifest artifacts must be objects")
    if set(candidate_artifacts) != set(before_artifacts):
        raise ValueError("staleness refresh changed the manifest artifact key set")
    for name, value in before_artifacts.items():
        if name != "numerics" and candidate_artifacts.get(name) != value:
            raise ValueError(
                f"staleness refresh changed unrelated artifacts.{name} state"
            )

    before_numerics = before_artifacts.get("numerics")
    candidate_numerics = candidate_artifacts.get("numerics")
    if not isinstance(before_numerics, dict) or not isinstance(candidate_numerics, dict):
        raise ValueError("manifest artifacts.numerics must be objects")
    before_analyses = before_numerics.get("analyses")
    candidate_analyses = candidate_numerics.get("analyses")
    if not isinstance(before_analyses, list) or not isinstance(candidate_analyses, list):
        raise ValueError("manifest numerics analyses must be arrays")
    if len(before_analyses) != len(candidate_analyses):
        raise ValueError("staleness refresh changed the analysis registry size")

    for old, new in zip(before_analyses, candidate_analyses, strict=True):
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ValueError("manifest numerics analyses must contain objects")
        old_without_status = {key: value for key, value in old.items() if key != "status"}
        new_without_status = {key: value for key, value in new.items() if key != "status"}
        if old_without_status != new_without_status:
            raise ValueError(
                "staleness refresh changed analysis-owned evidence or dependencies"
            )
        transition = (old.get("status"), new.get("status"))
        if transition not in {
            ("done", "done"),
            ("done", "stale"),
            ("partial", "partial"),
            ("partial", "stale"),
            ("stale", "stale"),
            ("failed", "failed"),
            ("blocked", "blocked"),
            ("skipped", "skipped"),
            ("in_progress", "in_progress"),
            ("not_started", "not_started"),
        }:
            raise ValueError(f"unsupported numerics staleness transition {transition!r}")


def _validate_intrinsic_scan_evidence(
    helper: Any,
    project_dir: Path,
    candidate: dict[str, Any],
) -> None:
    numerics = candidate.get("artifacts", {}).get("numerics", {})
    analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else []
    for analysis in analyses:
        if not isinstance(analysis, dict) or analysis.get("status") not in {
            "done",
            "partial",
            "stale",
        }:
            continue
        analysis_id = analysis.get("analysis_id")
        if not isinstance(analysis_id, str):
            raise ValueError("numerics analysis is missing analysis_id")
        metadata_path = (
            project_dir
            / "numerics"
            / "scan-results"
            / analysis_id
            / "scan.meta.json"
        )
        metadata = helper.load_json(metadata_path)
        snapshot = metadata.get("scan_config_snapshot") if isinstance(metadata, dict) else None
        if not isinstance(snapshot, dict):
            raise ValueError(
                f"{analysis_id} lacks an intrinsic scan_config_snapshot for stale validation"
            )
        issues = helper.validate_scan_artifact_pair(
            project_dir,
            analysis_id,
            historical_scan_config_snapshot=snapshot,
        )
        if issues:
            raise ValueError(
                f"{analysis_id} intrinsic historical evidence is invalid: "
                + "; ".join(issues)
            )


def validate_candidate(
    helper: Any,
    repo_root: Path,
    project_dir: Path,
    before: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    _validate_active_inputs(helper, repo_root, project_dir, before)
    expected = build_candidate(helper, before, project_dir=project_dir)
    if candidate != expected:
        raise ValueError("staleness candidate is not the exact pure derived projection")
    _validate_narrow_transition(before, candidate)
    schema_errors = _manifest_schema_errors(helper, repo_root, candidate)
    if schema_errors:
        raise ValueError(
            "staleness candidate failed manifest schema validation: "
            + "; ".join(schema_errors)
        )
    _validate_intrinsic_scan_evidence(helper, project_dir, candidate)


def _input_identities(project_dir: Path) -> dict[str, Any]:
    return {
        "manifest": capture_identity(project_dir / "manifest.json"),
        "model": capture_identity(project_dir / "model" / "model-spec.json"),
        "constraints": capture_identity(
            project_dir / "constraints" / "constraints-data.json"
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    project_dir = args.project_dir.resolve()
    manifest_path = project_dir / "manifest.json"

    try:
        helper = load_manifest_helper(repo_root)
        with publication_lock(
            project_dir,
            "numerics-staleness-refresh",
            blocking=args.write,
        ) as lock:
            identities = _input_identities(project_dir)
            manifest = helper.load_json(manifest_path)
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json must contain an object")
            candidate = build_candidate(helper, manifest, project_dir=project_dir)
            validate_candidate(helper, repo_root, project_dir, manifest, candidate)

            if candidate == manifest:
                print(f"OK {manifest_path}: numerics stale projection is current")
                return 0
            if not args.write:
                print(
                    f"NEEDS REFRESH {manifest_path}: rerun with --write to publish "
                    "the derived numerics stale projection"
                )
                return 1

            with PublicationTransaction.begin(
                project_dir,
                "numerics-staleness-refresh",
                lock=lock,
            ) as transaction:
                staged_manifest = transaction.stage_path("manifest.json")
                helper._write_staged_manifest_candidate(staged_manifest, candidate)
                transaction.add(
                    staged_manifest,
                    manifest_path,
                    mode="replace",
                    expected_before=identities["manifest"],
                )

                def verify_inputs() -> None:
                    current = _input_identities(project_dir)
                    if current != identities:
                        raise ValueError(
                            "model, constraints, or manifest changed before stale publication"
                        )

                def verify_published() -> None:
                    published = helper.load_json(manifest_path)
                    if published != candidate:
                        raise ValueError("published manifest differs from stale candidate")
                    _validate_intrinsic_scan_evidence(helper, project_dir, candidate)

                transaction.commit(
                    validate_candidate=lambda: validate_candidate(
                        helper,
                        repo_root,
                        project_dir,
                        manifest,
                        candidate,
                    ),
                    pre_publish_check=verify_inputs,
                    post_publish_check=verify_published,
                )
    except TransactionCommittedCleanupError as exc:
        print(
            "warning: numerics stale projection committed successfully, but private "
            f"cleanup is pending for transaction {exc.transaction_id}: "
            f"{exc.cleanup_error}. Do not retry this command; use "
            "recover_publication_transactions.py for the same project.",
            file=sys.stderr,
        )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"REFRESHED {manifest_path}: numerics stale projection published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
