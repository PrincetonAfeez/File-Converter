# Service Level Objectives

Targets for the core customer workflow. Measured over a rolling 28-day window. Breaching an
objective consumes the error budget; exhausting the budget freezes non-critical rollouts
until the service recovers.

## Availability

| Surface | SLI | SLO | Error budget (28d) |
|---|---|---|---|
| Web UI (`/`, job pages) | successful (non-5xx) responses ÷ total | 99.5% | ~3h 21m |
| Login (`/accounts/login/`) | successful responses ÷ total | 99.5% | ~3h 21m |
| Download (`/jobs/<id>/download/`) | successful responses ÷ total | 99.9% | ~40m |
| Readiness (`/ops/ready/`) | 200 responses ÷ probes | 99.9% | ~40m |

## Latency (server-side, excludes conversion time)

| Surface | SLI | SLO |
|---|---|---|
| Page loads (dashboard, job list) | p95 response time | < 400 ms |
| Job status poll (`/jobs/<id>/status/`) | p95 response time | < 200 ms |
| Upload accept (enqueue, not conversion) | p95 response time | < 1 s |

## Conversion pipeline

| SLI | SLO |
|---|---|
| Jobs reaching a terminal state (not stuck) | 99.9% |
| Median queue wait (enqueue → worker claim) | < 10 s at nominal load |
| Dead-letter rate (jobs exhausting retries) | < 0.5% of submissions |

## Measurement & alerting

- Availability/latency: derive from access logs / a metrics pipeline scraping `/ops/metrics`.
- Queue wait & dead-letters: `job.*` `JobEvent` timestamps and `conversion.job.dead_letter`
  events; `/ops/metrics` exposes `dead_letter_total`, `outbox_failed_total`,
  `outbox_pending_total`, and `queue_depth`.
- Page a human when: readiness fails, availability burn-rate is 2%+/hour, or queue backlog
  exceeds `FILECONVERTER_QUEUE_BACKLOG_ALERT` (see `OPERATIONS.md`).

## Explicitly not covered

Conversion *duration* is not an SLO — it depends on file size and the external tool
(LibreOffice/FFmpeg). Progress is surfaced to the user instead.
