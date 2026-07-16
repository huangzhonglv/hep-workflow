# Smoke E2E Literature Reproduction Inputs

This fictional paper defines the same toy scalar observable used by the
`smoke-e2e` workspace: `BR_toy = 1.0e-4 * (v_Delta / M_Hpp)^2`.

The reproduction fixture contains two targets. `target-001` is a complete
figure-curve target at fixed `v_Delta = 0.001`. `target-002` intentionally
omits the fixed `v_Delta` value in `paper-extract.json` so the
repro-orchestrator can mark it as `target_will_be_blocked` before numerics.

Each quantitative target retains a raw acquisition snapshot, a distinct
canonical-unit CSV, and a machine-verifiable normalization record containing
the acquisition identity, exact conversions, and SHA-256 checksums. The
comparator consumes only the canonical CSV; the raw file and record are
integrity evidence, not alternate metric inputs.
