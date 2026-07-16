#!/usr/bin/env bash
# Wolfram-aware end-to-end smoke for hep-numerics.
# Requires wolframscript on PATH and hard-fails when it is unavailable.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/.." && pwd -P)"

if ! command -v wolframscript >/dev/null 2>&1; then
    echo "ERROR: wolframscript not on PATH; this script is wolframscript-aware" >&2
    echo "       and not appropriate for environments without Wolfram." >&2
    echo "       Use scripts/smoke_hep_numerics.sh for the canonical baseline." >&2
    exit 1
fi

exec make -C "$repo_root" validate e2e
