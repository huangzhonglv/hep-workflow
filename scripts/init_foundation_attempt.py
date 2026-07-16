#!/usr/bin/env python3
"""Allocate and seed one private foundation-skill publication attempt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from _foundation_publication import MODE_SPECS, initialize_attempt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Allocate a private candidate tree for hep-idea or "
            "hep-paper-formalize without editing authoritative artifacts."
        )
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument(
        "--owner",
        required=True,
        choices=sorted({owner for owner, _ in MODE_SPECS}),
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        attempt = initialize_attempt(
            args.project_dir,
            owner=args.owner,
            mode=args.mode,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "status": "allocated",
        "owner": attempt.owner,
        "mode": attempt.mode,
        "project_dir": attempt.project_dir.as_posix(),
        "attempt_id": attempt.attempt_id,
        "attempt_dir": attempt.attempt_dir.as_posix(),
        "candidate_dir": attempt.candidate_dir.as_posix(),
    }
    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
    else:
        print("foundation attempt allocated")
        for key in (
            "owner",
            "mode",
            "project_dir",
            "attempt_id",
            "attempt_dir",
            "candidate_dir",
        ):
            print(f"  - {key}: {payload[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
