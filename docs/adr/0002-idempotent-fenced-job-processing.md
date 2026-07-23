# 2. Idempotent, fenced job processing

Date: 2026-07-09
Status: Accepted

## Context

Celery delivers at-least-once with `acks_late`, so a job can be delivered to more than one
worker (redelivery, stale reclaim). Naive processing would double-run conversions and could
produce duplicate outputs or double-billing.

## Decision

- Each job carries a monotonic `claim_generation`. `claim_job` takes a `SELECT FOR UPDATE`
  lock, transitions the row to `PROCESSING`, and increments the generation ("fencing token").
- Every subsequent write (progress, promotion, terminal transition) is guarded by
  `(pk, claim_generation, status=PROCESSING)`; a superseded worker's writes affect 0 rows.
- Submissions are idempotent via a unique `(owner, workspace, idempotency_key)` constraint.
- Terminal side effects (usage ledger, outbox) are emitted once, keyed by job + status.

## Consequences

- Duplicate delivery is safe; only the current claim owner can mutate a job.
- Requires a backend that honours row locks (PostgreSQL); on SQLite the lock is a no-op and
  concurrency safety degrades to best-effort (dev only).
