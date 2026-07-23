# 5. Blob lifecycle and garbage collection

Date: 2026-07-09
Status: Accepted

## Context

Uploaded inputs and generated outputs are large. Storage grows unbounded without retention,
and non-transactional storage writes can orphan files (crash between file write and row
commit; output written just before a fenced/cancelled promotion).

## Decision

- Outputs expire on a TTL (`expire_due_outputs`): bytes deleted, `deleted_at` stamped, job
  marked `expired`.
- Input bytes for terminal jobs are purged after their own TTL (`purge_terminal_input_files`)
  while keeping the row for audit.
- A `garbage_collect_blobs` sweep deletes `FileBlob`s referenced by no job (orphans) past a
  GC window.
- The input blob is checksummed and persisted in a single write; failures during submission
  drop the blob (row + file).

## Consequences

- Storage is bounded and orphans are reclaimed by scheduled tasks.
- Retention windows are configurable; deletions are irreversible (gated by backups per
  `OPERATIONS.md`).
