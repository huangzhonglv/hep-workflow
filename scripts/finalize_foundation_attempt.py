#!/usr/bin/env python3
"""Validate and transactionally publish one foundation-skill candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from _foundation_publication import MODE_SPECS, finalize_with_cleanup_status


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an owned hep-idea/hep-paper-formalize candidate and "
            "atomically publish its owner roots plus manifest-last projection."
        )
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--owner",
        required=True,
        choices=sorted({owner for owner, _ in MODE_SPECS}),
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = (
        args.repo_root.resolve()
        if args.repo_root is not None
        else Path(__file__).resolve().parent.parent
    )
    try:
        result = finalize_with_cleanup_status(
            repo_root,
            args.project_dir,
            args.attempt_dir,
            owner=args.owner,
            mode=args.mode,
            attempt_id=args.attempt_id,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "status": result.status,
        "owner": result.attempt.owner,
        "mode": result.attempt.mode,
        "attempt_id": result.attempt.attempt_id,
        "project_dir": result.attempt.project_dir.as_posix(),
        "cleanup_pending": result.cleanup_pending,
    }
    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"foundation publication {result.status}")
        for key in ("owner", "mode", "attempt_id", "project_dir", "cleanup_pending"):
            print(f"  - {key}: {payload[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
