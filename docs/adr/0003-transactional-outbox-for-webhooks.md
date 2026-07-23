# 3. Transactional outbox for webhook delivery

Date: 2026-07-09
Status: Accepted

## Context

Customers need conversion lifecycle events (done/failed/dead-letter). Emitting webhooks
inline with the DB transaction risks either lost events (send before commit that rolls back)
or phantom events (commit succeeds, send fails).

## Decision

- Terminal transitions write an `OutboxEvent` row in the same transaction as the state change
  (keyed by an idempotency key so re-emission is a no-op).
- A periodic `deliver_outbox_events` task POSTs undelivered events to the configured webhook
  with capped retries, HMAC-SHA256 signatures (`X-Signature`), scheme validation, and no
  redirect following; delivered rows are purged after a retention window.

## Consequences

- Delivery is **at-least-once**; consumers must dedupe on `X-Idempotency-Key`.
- No event is lost on a crash between commit and delivery — the relay retries.
- Delivery requires a shared broker/DB; unconfigured webhook URL means events accumulate as
  pending (delivered once a URL is set).
