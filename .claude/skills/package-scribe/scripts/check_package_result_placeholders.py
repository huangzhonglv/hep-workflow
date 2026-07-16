#!/usr/bin/env python3
"""Reject unresolved template placeholders in package-scribe result files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence


MANAGED_RESULT_FILES = (
    "request.md",
    "result-summary.md",
    "run-instructions.md",
    "result-python.py",
    "result-meta.json",
)
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*[A-Za-z0-9_]+\s*\}\}")


class ExitOneArgumentParser(argparse.ArgumentParser):
    """Keep the shell helper's exit status for malformed invocations."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = ExitOneArgumentParser(
        description="Check package-scribe result files for unresolved placeholders."
    )
    parser.add_argument("result_dir", help="Package-scribe result directory.")
    return parser.parse_args(argv)


def find_placeholders(files: Sequence[Path]) -> list[str]:
    matches: list[str] = []
    include_filename = len(files) > 1

    for path in files:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if PLACEHOLDER_PATTERN.search(line):
                prefix = f"{path}:" if include_filename else ""
                matches.append(f"{prefix}{line_number}:{line}")
    return matches


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result_dir = Path(args.result_dir).expanduser().resolve()

    if not result_dir.is_dir():
        print(f"result directory not found: {result_dir}", file=sys.stderr)
        return 1

    files = [
        result_dir / filename
        for filename in MANAGED_RESULT_FILES
        if (result_dir / filename).is_file()
    ]
    if not files:
        print(f"no managed result files found under: {result_dir}", file=sys.stderr)
        return 1

    try:
        matches = find_placeholders(files)
    except (OSError, UnicodeError):
        print("placeholder scan failed", file=sys.stderr)
        return 2

    if matches:
        print("\n".join(matches))
        print(file=sys.stderr)
        print(
            f"ERROR: unresolved template placeholders found in {result_dir}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no unresolved template placeholders found in {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
