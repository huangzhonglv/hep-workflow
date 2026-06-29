#!/usr/bin/env bash
# Wolfram-aware end-to-end smoke for hep-numerics.
# Requires wolframscript on PATH; will hard-fail otherwise (per Prompt 8).
set -euo pipefail

if ! command -v wolframscript >/dev/null 2>&1; then
    echo "ERROR: wolframscript not on PATH; this script is wolframscript-aware" >&2
    echo "       and not appropriate for environments without Wolfram." >&2
    echo "       Use scripts/smoke_hep_numerics.sh for the wolframscript-free path." >&2
    exit 1
fi

# Reuse the wolframscript-free baseline first to fail fast on regressions.
# Note: this runs in a subshell, so any venv it activates does NOT
# propagate back to this script.
bash scripts/smoke_hep_numerics.sh

# Re-source the venv created/used by the baseline before invoking
# pytest from this shell; otherwise we'd hit system python which may
# lack pytest / the deps installed by the baseline.
# shellcheck disable=SC1091
source .venv/bin/activate

# Then run the gated e2e suite.
python3 -m pytest tests/e2e -x --run-e2e
