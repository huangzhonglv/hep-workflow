#!/usr/bin/env python3
"""Print deterministic, read-only reproduction target readiness as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _reproduction_readiness import derive_reproduction_readiness
from compare_to_reference import validate_target_normalization


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Derive schema-valid per-target reproduction readiness without "
            "modifying the workspace project."
        )
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Workspace project directory.",
    )
    parser.add_argument(
        "--analysis-id",
        required=True,
        help="Analysis id used for numeric targets, e.g. analysis-001.",
    )
    parser.add_argument(
        "--target-id",
        help="Optional single reproduction target id.",
    )
    return parser.parse_args(argv)


def _validate_reference(
    project_dir: Path,
    target: dict[str, object],
    paper_id: str,
) -> None:
    validate_target_normalization(
        project_dir,
        target,
        paper_id=paper_id,
    )


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = derive_reproduction_readiness(
            args.project_dir,
            args.analysis_id,
            target_id=args.target_id,
            reference_validator=_validate_reference,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
