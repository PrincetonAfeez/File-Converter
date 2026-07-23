# Load & capacity testing

Load-test evidence for the core convert path, mapped to the SLOs in `../docs/SLO.md`.

## Tool

[k6](https://k6.io) — a single static binary, no repo dependency.

## Run (against staging only)

```bash
k6 run \
  -e BASE_URL=https://staging.example.com \
  -e USER=<demo-user> -e PASS=<demo-pass> \
  -e VUS=20 -e DURATION=2m \
  k6-convert.js
```

The script asserts the SLO thresholds directly (`upload_latency p95<1000ms`,
`status_latency p95<200ms`, `errors rate<0.5%`) and exits non-zero if they are breached, so it
can gate a release in CI (see `.github/workflows/loadtest.yml`, run manually / on a schedule).

## What to record per run

- Date, target build/commit, VUs, duration.
- p50/p95/p99 for upload and status; error rate; broker `queue_depth` peak (`/ops/metrics/`).
- Whether thresholds passed; if not, the bottleneck (DB, worker count, broker).

Keep results alongside the release so capacity trends are visible over time.
