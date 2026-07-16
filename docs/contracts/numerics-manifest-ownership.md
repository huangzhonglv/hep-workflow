# Numerics Manifest Ownership

Project-level rule: `manifest_version = 2` stores numerics evidence per
analysis. Updating one analysis must not replace or erase another analysis's
files or dependency snapshot. Unrelated fields remain byte-identical except
that an entry whose exact recorded inputs no longer match current bytes is
conservatively relabeled `stale`.

## Per-analysis source of truth

`artifacts.numerics.analyses` is a sorted array of objects. Each object owns:

- one canonical `analysis_id`;
- its evidence `status`;
- its sorted project-relative `files`;
- the model, calculation-task, and constraints dependency snapshot used by
  that scan;
- its producer and UTC timestamp.

Every evidence-bearing analysis owns its canonical scan config, `scan.csv`,
`scan.meta.json`, and analysis summary. An analysis with published figures also
owns `numerics/figures/{analysis_id}/figures.meta.json` and every exact output
listed by that sidecar; unproven or stale extra files fail validation. Figure
paths are owned only by their analysis. The aggregate `files` is the sorted
union of entry files. Aggregate
`produced_by` and `timestamp` come from the deterministically latest entry.

The direct real, canonical `analysis-NNN` directories under
`numerics/scan-results/` are the disk-side published-analysis set. That set is
exactly equal to the registry entries whose status is `done`, `partial`, or
`stale`; an unregistered result directory and an evidence-bearing entry without
its result directory both fail validation. Transaction staging and allocation
reservations live outside `scan-results`. A scan config by itself is an allowed
initialization draft, so `scan-configs/*.json` is not used to invent a published
analysis owner.

## Aggregate status reducer

The conservative precedence is:

1. `failed`
2. `blocked`
3. `stale`
4. `done` only when every entry is `done`
5. otherwise `partial`

An empty registry is `not_started` with empty files and null aggregate producer
and timestamp. A previously current-looking `done` or `partial` entry becomes
`stale` when any exact execution dependency recorded by its verified scan graph
drifts, including model, calculation, constraint, execution configuration, or
bound code bytes. Renderer-only live config changes do not rewrite that frozen
scan graph; a successful replot binds the live rendering request and immutable
scan pair through a new verified figure graph.
Failure, blocked, skipped, in-progress, and not-started states remain
non-success states rather than having their diagnostic meaning overwritten by
`stale`.

Stale means current-input mismatch; it does not excuse corrupt historical
evidence. Finite values, complete grids, verdict/count consistency, exact CSV
hash, internally consistent exact config source/snapshot, and summary markers
remain mandatory.

## Writer and migration rules

`run_scan.py` and `make_figures.py` build a pure merged candidate under the
project publication lock and transactionally publish the manifest last. The
shared serializer refuses every path outside a private transaction staging
directory, so it cannot directly write the live manifest. New numerics history
events require a globally fresh event ID so two genuine events in the same
timestamp second do not collapse. Validators permit a missing ID only as an
explicit legacy-read compatibility path; they reject duplicate IDs and unknown
or ambiguous analysis linkage.

An upstream model or constraints publication must rederive staleness in the
same locked generation. `finalize_foundation_attempt.py` does this from its
staged upstream bytes before publishing the manifest. For a legacy or
externally completed upstream transition,
`scripts/refresh_numerics_staleness.py` is the only standalone repair path: it
is read-only unless `--write` is supplied, verifies live model/constraint
identity and intrinsic historical scan evidence, permits only `done|partial ->
stale` plus the deterministic aggregate projection, and transactionally
publishes `manifest.json`. This derived relabeling does not append a numerics
history event and never makes stale evidence current again.

Version 1 migration is explicit through `scripts/migrate_manifest_v2.py`.
Without `--write` it only diagnoses. Migration rejects ambiguous state,
missing history ownership, schema-invalid or incomplete intrinsic evidence,
an incomplete provenance graph, and an empty analysis registry whose legacy
aggregate is not the exact initial empty skeleton. Legacy aggregate
files/dependencies/status/producer/timestamp must reconcile with the
deterministically latest reconstructed analysis; migration never discards a
legacy file or failed/blocked state to make a v2 manifest validate. The complete
workspace with the staged v2 candidate must validate before publication.
