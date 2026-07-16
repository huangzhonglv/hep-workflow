# Transactional State Publication

Project-level rule: a command that publishes more than one authoritative path
must expose either the complete new generation or the complete prior
generation. A caught error, concurrent writer, or interrupted process must not
leave a mixed generation that a reader can mistake for valid evidence.

## Writer protocol

Writers use the shared `scripts/_publication_transaction.py` protocol:

1. acquire the publication-anchor lock before reading shared state used in a
   merge;
2. build every candidate below the private same-filesystem transaction tree;
3. validate staged scientific evidence before publication;
4. capture strong destination identities and publish with compare-and-swap;
5. write the manifest last when it indexes other candidate paths;
6. durably journal, `fsync`, and either commit the full generation or restore
   only paths still proven to be owned by the transaction;
7. never replace a regular file with a directory or a directory with a file;
8. never infer that an unknown or changed destination is safe to delete.

Immutable reproduction runs use `create_only`. Scan reruns, replots, package
initialization, analysis initialization, and manifest migration use the same
protocol with the mode appropriate to their ownership contract.

### Package-Scribe batch results

Batch generation never writes into `calculations/task-NNN/` in place. The
initializer atomically reserves an owned directory below the project-local
`.hep-workflow-package-attempts/` root and records the final task identity and
an unguessable attempt/event token before templates are created. All generation
and benchmark work occurs in that non-authoritative attempt.

`finalize_package_result.py` accepts only the exact task/attempt/token tuple. It
copies the complete candidate into private transaction staging, rejects
incomplete or unsafe trees, mechanically rebuilds and verifies exact-byte
provenance against a candidate overlay, validates result semantics, and builds
the calculation manifest merge under the project lock. It then publishes the
complete task tree, the attempt outcome, and `manifest.json` (last) in one
journaled compare-and-swap transaction. Generation, validation, or publication
failure preserves both the prior task tree and prior manifest. A blocked,
partial-translation, or failed-translation attempt is diagnostic evidence only
and cannot replace a completed result or enter `calculations.completed_tasks`.
When the prior calculation aggregate is explicitly `stale`, the first
successful rerun starts a new current generation: only that rerun enters
`completed_tasks`, every other currently declared task remains pending, and
the aggregate dependency is rebound to the active model. Preserved task
directories not in the new `completed_tasks` set remain historical evidence;
they are never promoted merely because one sibling task was rerun.

The finalizer, not the orchestrator, owns the task-scoped calculation manifest
event. A successful retry of an already-published attempt verifies the recorded
task identity and event ID and reports `already_published`; it does not append a
duplicate event or republish the task.

### Foundation-skill results

`hep-idea` and `hep-paper-formalize` never generate into authoritative
`idea/`, `model/`, `constraints/`, `literature/`, or `manifest.json` paths.
`scripts/init_foundation_attempt.py` allocates a private, owner/mode-bound
candidate below `.hep-workflow-foundation-attempts/` and records exact baseline
identities. The skill writes only below the returned `candidate_dir`.

`scripts/finalize_foundation_attempt.py` accepts only that exact project,
attempt, owner, mode, and unguessable token. Under the project lock it rejects
cross-owner manifest changes, rewritten history, unsafe candidate trees,
history actions that do not match the actual changed file scopes, implicit
deletion, invalid schemas or cross-file identities, concurrent baseline drift,
and corrupt historical scan evidence. It mechanically marks an evidence-bearing
calculation aggregate `stale` when `model-spec.json`, `calc-tasks.json`, or
`benchmarks.json` changes, preserving its task registry, producer, timestamp,
and historical dependency. It also derives the numerics stale projection from
the staged model and constraints plus changed calculation inputs, then
publishes changed owner files, the closed attempt outcome, and `manifest.json`
last in one journaled compare-and-swap transaction. Existing hidden or
otherwise unowned files are not candidate inputs and are never deleted.

The skill owns the scientific content and its allowed history-event intent;
the finalizer owns authoritative publication and does not append a second
event. A successful retry verifies the published identities and reports
`already_published`. A generation, validation, or caught publication failure
leaves the prior authoritative generation intact.

## Reader protocol

Readers that combine multiple authoritative paths must hold the same project
publication lock while loading and hashing one coherent input generation. A
writer may run while a long calculation is in progress, but publication must
reacquire the lock and verify the captured exact-byte dependency graph before
committing. A validator uses a non-blocking coherent-reader lock and fails
explicitly on live contention or an incomplete journal.

Checking once for a transaction and then reading without a lock is not a
coherent snapshot.

## Interrupted transaction recovery

Normal writers and validators fail closed while a private journal remains.
An active journal is never its own proof that recovery may move authoritative
paths. Every journal generation has a create-only attestation under the sibling
`.hep-workflow-transactions/.active-owners/` directory. The closed-schema
attestation binds the random cleanup token, transaction ID and scope,
transaction-directory device/inode, journal generation, and exact journal-byte
SHA-256. Recovery rejects a missing, malformed, duplicated, hash-mismatched, or
inode-mismatched attestation before interpreting any journal entry or moving any
destination. A valid-looking transaction directory or journal without this
external evidence is preserved as `blocked`.

The attestation is written and directory-`fsync`ed before its journal generation
is atomically installed. Therefore a crash between those operations leaves the
previous journal generation authenticated and recoverable; a journal can never
become actionable before its matching attestation is durable. On cleanup, the
garbage ownership record is made durable first, the transaction directory is
renamed into garbage and both parents are `fsync`ed, and only then are active
attestations retired. A crash in that window leaves at least one independently
authenticated recovery path and never turns journal text into deletion
authority.

Inspect without mutation:

```bash
python3 scripts/recover_publication_transactions.py \
  --project-dir workspace/projects/<project> --format json
```

After reviewing the journal, request conservative recovery explicitly:

```bash
python3 scripts/recover_publication_transactions.py \
  --project-dir workspace/projects/<project> --recover --format json
```

`rolled_back` and `finalized` are successful typed outcomes. `blocked` means
filesystem ownership is ambiguous; the tool makes no destructive guess and
returns nonzero. Never remove `.hep-workflow-transactions` manually merely to
make a command proceed.

After a durable commit, private state is first renamed into a typed garbage
quarantine and only then deleted. If deletion is interrupted, the command
reports “committed; cleanup pending” as a successful publication with a
warning. Re-running the scientific command is not recovery and must not create
a second event; use the recovery command to finish private cleanup.
The quarantine basename alone never authorizes deletion. A durable ownership
record outside the recursively deleted tree binds a random journal token,
outcome, basename, device, and inode. Missing, malformed, duplicated, or
inode-mismatched ownership evidence is `blocked` and is preserved for manual
inspection.

## Allocation and reservation

Numbered IDs are claimed with atomic `mkdir(exist_ok=False)` and typed owner /
attempt metadata. Allocate-new and open-existing are distinct operations.
Missing, corrupt, failed, or abandoned reservations remain occupied until an
explicit authenticated recovery policy handles them; file absence alone never
authorizes recycling. Shared custom files and manifest merges remain protected
by the publication lock after an ID is reserved.

## Supported platform and filesystem

The current durable implementation supports POSIX hosts with `fcntl.flock`,
regular local filesystem objects, same-filesystem atomic rename, file `fsync`,
and directory `fsync`. Callers are responsible for placing a workspace on a
qualified local filesystem; the helper cannot reliably identify every remote
or userspace mount from a path. The advisory lock is held directly on an open file
descriptor for the resolved publication-anchor directory, so its identity does
not depend on `TMPDIR`, path aliases, user-specific lock roots, or an unlinkable
sidecar file. It fails explicitly when a required primitive reports that it is
unavailable, but absence of that error is not proof that a mount has local-disk
semantics. Windows, NFS, SMB, object-backed mounts, and cross-device publication
are unsupported unless separately qualified by tests and this contract is
updated.
