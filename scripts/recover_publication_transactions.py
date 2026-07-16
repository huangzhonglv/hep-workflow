#!/usr/bin/env python3
"""Inspect or explicitly recover interrupted hep-workflow publications."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _publication_transaction import (
    active_transactions,
    pending_transaction_cleanups,
    recover_incomplete_transactions,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List interrupted publication journals. Recovery is read-only unless "
            "--recover is explicitly supplied."
        )
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="Existing workspace project (or publication anchor) to inspect.",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Recover only journals whose filesystem ownership is unambiguous.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        anchor = args.project_dir.expanduser().resolve(strict=True)
        if not anchor.is_dir():
            raise NotADirectoryError(anchor)
        if args.recover:
            results = recover_incomplete_transactions(anchor)
            payload = [
                {
                    "transaction_id": result.transaction_id,
                    "outcome": result.outcome,
                    "issues": list(result.issues),
                }
                for result in results
            ]
            unresolved = any(item["outcome"] == "blocked" for item in payload)
        else:
            payload = [
                {
                    "transaction_id": transaction_id,
                    "outcome": "active",
                    "issues": [],
                }
                for transaction_id in active_transactions(anchor)
            ]
            payload.extend(
                {
                    "transaction_id": transaction_id,
                    "outcome": "cleanup_pending",
                    "issues": [f"safe private {outcome} tree awaits deletion"],
                }
                for transaction_id, outcome in pending_transaction_cleanups(anchor)
            )
            unresolved = bool(payload)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
    elif not payload:
        print("OK: no incomplete publication transactions")
    else:
        for item in payload:
            print(f"{item['outcome'].upper()} {item['transaction_id']}")
            for issue in item["issues"]:
                print(f"  - {issue}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
