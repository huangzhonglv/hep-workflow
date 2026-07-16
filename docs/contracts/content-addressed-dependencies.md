# Content-Addressed Dependency Contract

Scientific outputs are trustworthy only when every file that can affect them is
identified by a safe canonical path and the SHA-256 of its exact bytes. Version
labels, task IDs, manifest checksum strings, and file existence are descriptive
metadata; they are not substitutes for recomputing content identities.

## Graph format

The reusable graph schema is `schemas/dependency-graph.schema.json`. A verified
graph has:

- `version = "sha256-bytes-v1"`;
- `verification_status = "verified"`;
- a nonempty `entries[]` array;
- one `root_sha256` over the canonical graph payload.

Each entry has exactly four fields:

- `scope`: `project` for a path below the workspace project root or
  `repository` for a path below the hep-workflow repository root;
- `role`: a nonempty, whitespace-free semantic role assigned by the consumer's
  dependency-coverage policy;
- `path`: a root-relative POSIX path;
- `sha256`: lowercase `sha256:<64 hex>` for the file's exact bytes.

Entries are stored in ascending `(scope, path, role)` order. The same tuple may
appear only once. Two entries must not identify the same filesystem object,
including through hard links.

## Exact-byte and root hashing

File hashes are computed over bytes exactly as stored. JSON key order,
whitespace, line endings, CSV formatting, comments, and generated-code text are
not normalized or ignored. A one-byte change therefore changes the file hash.

The root hash excludes `root_sha256` itself. It is SHA-256 over the UTF-8 bytes
of this payload:

```json
{
  "entries": [],
  "verification_status": "verified",
  "version": "sha256-bytes-v1"
}
```

Before hashing, `entries` is sorted by `(scope, path, role)`. Serialization uses
Python `json.dumps` with `sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=False`, and `allow_nan=False`, with no trailing newline.

## Path and filesystem rules

Persisted paths are never absolute and never contain a backslash, empty
component, `.` component, or `..` component. Every path must resolve below the
root selected by its scope. Every component below that trusted root is checked
with `lstat`; symlinks and non-regular final files are rejected. A resolved path
escape, duplicate dependency key, or inode/hard-link alias is a hard error.

Paths are not case-folded or Unicode-normalized. New machine identifiers and
repository-controlled paths should remain ASCII for portability. The graph
records the exact relative spelling selected by the producer and independently
expected by the consumer.

## Coverage and verification

`scripts/_dependency_graph.py` is the mechanical implementation. Producers use
`make_spec` and `build_dependency_graph`. Consumers independently derive their
complete expected `DependencySpec` set and call `verify_dependency_graph` with
`expected_specs`; the recorded and expected `(scope, path, role)` sets must be
exactly equal. `required_roles` may add workflow-specific role gates, but it is
not a substitute for exact expected coverage.

Verification recomputes every file hash and the canonical root. Consumers must
not trust the producer's entry list as proof that coverage is complete. A
workflow that hashes before execution must recheck the same dependency graph
immediately before publishing output so mid-run input drift cannot be blessed.

The shared helper intentionally does not discover model, calculation,
constraint, numerics, literature, or tool dependencies. Each producer/consumer
contract owns that exact role/path inventory; omitting a load-bearing input is a
contract defect, not an optional graph optimization.

## Legacy state

A historical artifact without a complete graph may be represented only as:

```json
{
  "version": "sha256-bytes-v1",
  "verification_status": "legacy-unverified",
  "reason": "precise explanation of why exact dependencies cannot be verified"
}
```

Legacy graphs contain neither `entries` nor `root_sha256`. Verification rejects
them unless the caller explicitly sets `allow_legacy=True`. Allowing inspection
does not promote the artifact: `legacy-unverified` evidence cannot support a new
scientific execution, an `independent` provenance claim, or a `pass` verdict.
Immutable historical reproduction runs are not rewritten; regeneration creates
new artifacts with verified graphs.

An explicitly `stale` calculation is different from an unverifiable legacy
artifact. Its recorded graph must still have canonical exact coverage, safe
existing paths, recorded hashes, and a valid root over those hashes. Validation
may skip only equality between recorded hashes and the current bytes that made
the artifact stale. This historical validation cannot support a current scan,
comparison, completion claim, or dependency rebind. A Package-Scribe rerun
creates a new current calculation generation; it never relabels untouched
historical task results as current.

## Integrated workflow boundaries

The Phase-1 integration binds the following artifact transitions:

- a new `calculations/<task-id>/result-meta.json` records the model/task
  inputs, calculation request and outputs, result schema, dependency helpers,
  and the repository-controlled `package-scribe` instructions/templates that
  generated it;
- a new `numerics/scan-results/<analysis-id>/scan.meta.json` records the scan
  config, model/tasks, consumed calculation metadata/code, constraints and
  interpolation tables, optional custom-observable module, scan runner,
  helpers, and relevant schemas; `scan.csv` has its own exact-byte checksum;
- a new `reproduction-result.json` records the current scientific inputs,
  selected calculation and scan artifacts, reference/normalization evidence,
  comparison code/helpers, relevant schemas, and optional plot style.

`scripts/_workflow_dependencies.py` owns those exact role/path inventories.
The calculation template/skill, scan runner, figure renderer, comparator, and
workspace validator build or independently verify the graph at their trust
boundaries. Calculation graphs are verified before a scan can consume a task;
scan graphs and the table checksum are verified before replot or comparison;
reproduction graphs are verified before publication and during workspace
validation. An arbitrary schema-valid entry list is therefore not sufficient.

Every new scan sidecar also embeds the exact UTF-8 scan-config source bytes
(decoded without newline normalization), their SHA-256, and the decoded
snapshot. Consumers prove that all three agree. A later replot may use a live
config whose execution projection still equals that frozen snapshot; the
renderer then records a separate exact graph over the live config, immutable
scan pair, renderer/helpers, schemas, model, and constraints in
`figures.meta.json`. Figure evidence cannot relabel or replace the scan's
scientific input graph.

Repository-controlled helper copies used by the standalone `hep-numerics`
installations are part of the mirror invariant. A helper change must be synced
before producing new graphs; otherwise mirror verification and dependent
artifact verification fail closed.

## Runtime and toolchain limits

Content identity is necessary provenance, not proof of execution semantics.
The current graphs bind repository-controlled code and declared data, but they
do not hash the Python interpreter, installed wheels/native libraries, OS/CPU,
Wolfram runtime, or Package-X installation. Scan metadata records selected
Python/scientific-package version strings for diagnosis; those strings are not
cryptographic environment attestations and do not promise bit-for-bit results
on a different platform.

Likewise, a graph proves neither that a recorded Package-X method executed on
the value that reached Python nor that an arbitrary custom observable is
deterministic. The honest-reproduction derivation ceiling remains in force
until runtime-verifiable execution evidence exists.

The scan `seed` drives an explicit local `numpy.random.PCG64` contract. The
runner derives independent smoke/scan, point-index, and canonical-consumer
substreams with `numpy.random.SeedSequence`; it passes a local
`numpy.random.Generator` only to a custom callable that explicitly declares the
`rng` keyword. The algorithm identifier/version, substream scheme, seed, and
consumer set are persisted in `scan.meta.json`, while the installed NumPy
version remains diagnostic environment metadata. Ambient Python/NumPy RNG,
entropy APIs, and dynamic-import attempts in executable backends fail
preflight. Partially seeding process-global state remains forbidden.
