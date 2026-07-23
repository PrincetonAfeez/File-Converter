# Operations Runbook

Operational procedures for running File Converter in production. Pairs with `README.md`
(setup) and `.env.example` (configuration).

## Environments

- **Local/dev** — SQLite + LocMemCache + `CELERY_TASK_ALWAYS_EAGER=True`. Row locks and
  cross-process cache are no-ops; use only for development.
- **Staging / pre-prod** — same image as prod, PostgreSQL + Redis + a Celery worker + beat.
  Run the smoke check below before promoting a build to production.
- **Production** — PostgreSQL (with `CONN_MAX_AGE`/PgBouncer), Redis, N web replicas behind
  a TLS-terminating proxy, ≥1 Celery worker, exactly one `beat` scheduler.

### Pre-prod smoke verification
```
python manage.py migrate
python manage.py check --deploy          # must be clean
python -m pytest -q                       # must pass
# then, against real infra: sign in, upload a CSV, confirm it reaches `done`, download it.
```

## Backups & restore (U9)

- **Backups**: run `scripts/backup.sh` on a schedule (cron / k8s CronJob). It dumps
  PostgreSQL (`pg_dump -Fc`) and archives `MEDIA_ROOT`. Ship the artifacts off-host (S3/GCS)
  and enable object-storage versioning + lifecycle. If using a managed database, also enable
  the provider's automated snapshots.
- **Restore**: `CONFIRM=yes scripts/restore.sh <db.dump> [media.tar.gz]`.
- **Restore drill (required)**: monthly, restore the latest backup into a scratch database,
  run `manage.py migrate`, and confirm row counts + a sample download. An untested backup is
  not a backup. Record the drill date/result.
- **RPO**: ≤ backup interval (default hourly). **RTO**: target < 1h (dominated by restore +
  media re-sync time). Tune the schedule to your RPO.

## Rollback (U10)

- Deploys are immutable container images tagged per release. To roll back, redeploy the
  previous image tag.
- **Migrations**: follow expand/contract — additive migrations first, deploy code, then a
  later contracting migration. Never combine a destructive schema change with the code deploy
  that depends on it, so the previous image keeps working during rollback.
- Django migrations are reversible where Django supports it: `python manage.py migrate <app>
  <previous_number>`. Constraint-adding migrations (e.g. `quotas/0002`) are not auto-reverted
  cleanly — take a backup before applying schema changes (backups gate destructive DDL).

## Caching strategy & invalidation

- **Backend**: `LocMemCache` in dev; set `FILECONVERTER_USE_REDIS_CACHE=True` for a shared
  Redis cache in production (required for cross-process correctness).
- **Job progress** (`apps/conversions/progress.py`): written by the worker under a 1h TTL and
  read by the web tier. Authoritative progress is also persisted to the DB, so a cache miss
  degrades gracefully — no explicit invalidation needed; entries expire by TTL and are
  overwritten by newer progress.
- **Feature flags** (`apps/ops/flags.py`): cached for 30s; flips take effect within that
  window. For an immediate flip, clear `flag:<name>` or restart.
- **Throttle counters** (login, upload): short-lived keys with their own windows; self-expire.
- **Invalidation principle**: all cache entries are TTL-bounded and derived from an
  authoritative store (DB/broker), so stale reads are self-healing. No manual purge is
  required on deploy; on a cache backend switch, simply let old entries lapse.

## Monitoring & alerting (U6, LAUNCH, GROWTH)

- **Metrics**: `GET /ops/metrics/` returns `queue_depth`, `job_status_counts`,
  `dead_letter_total`, `outbox_failed_total`, and `outbox_pending_total` as JSON for
  scraping (see `docs/monitoring/`). It is **not anonymous** — supply
  `Authorization: Bearer $FILECONVERTER_METRICS_TOKEN` from the scraper, or access it with
  a staff session. The `ops.monitor_queue_backlog` beat task warns when a queue exceeds
  `FILECONVERTER_QUEUE_BACKLOG_ALERT`. `/ops/health` and `/ops/ready` remain unauthenticated
  for orchestrator/uptime probes.
- **SLOs**: targets and error budgets in `docs/SLO.md`; alert on burn rate.

- **Health**: `GET /ops/health/` (liveness — always 200 when the process is up), `GET /ops/ready/`
  (readiness — checks DB, cache, broker, MIME scanner, RLS; returns **503** when any check is
  `error` or `degraded`). Wire **health** into container liveness (Docker Compose) and **ready**
  into load-balancer rotation / orchestrator readiness probes.
- **Synthetic uptime**: run `python scripts/check_readiness.py https://app.example.com` on a
  schedule (cron/k8s) or use `.github/workflows/uptime-synthetic.yml`. The workflow **fails**
  when the `STAGING_BASE_URL` repo secret is missing (no silent skip). Validate locally with
  `STAGING_BASE_URL=... python scripts/validate_deploy_env.py --require-uptime-url`.
  Import `docs/monitoring/prometheus-alerts.yml` into Alertmanager; maintain the rotation in
  `docs/monitoring/oncall.md`.
- **Load testing**: CI job `load-smoke` runs k6 against a real stack on every push/PR; full
  staging load tests via `loadtest/` and `.github/workflows/loadtest.yml` (manual).
- **Errors**: set `SENTRY_DSN` in production. With `DJANGO_DEBUG=False`, missing DSN fails
  `manage.py check --deploy` (`fileconverter.E001`) and marks `/ops/ready/` `sentry=degraded`
  (503). Opt out only with `FILECONVERTER_REQUIRE_SENTRY=False`. Every log line carries
  `req=<request-id>` (also returned as `X-Request-ID`).
- **Jobs**: watch for `job.dead_letter` events / `conversion.job.dead_letter` webhooks — a job
  that exhausted its retry budget needs human attention. Alert on their rate.
- **Outbox**: watch `outbox_failed_total` and `outbox.event.dead_letter` for webhook delivery
  exhaustion; pending backlog via `outbox_pending_total`.
- **Slow queries**: enabled by default in production (`FILECONVERTER_SLOW_QUERY_MS=500` when
  `DJANGO_DEBUG=False`); set to `0` to disable or raise the threshold.

## Incident response (LAUNCH)

1. **Detection** — uptime monitor / Sentry alert / dead-letter alert fires.
2. **Page** — the on-call engineer (rotation in `docs/monitoring/oncall.md`). Ack within 15 min.
3. **Triage** — check `/ops/ready/`, recent deploys, Sentry, worker/beat liveness, Redis/DB.
4. **Mitigate** — roll back the last deploy if it correlates; scale workers if queue backs up.
5. **Communicate** — post status to the customer status page and internal channel.
6. **Postmortem** — blameless writeup within 3 business days; file follow-up issues.

## Database role & row-level security

- **The application DB role must be a non-superuser without `BYPASSRLS`.** PostgreSQL
  superusers ignore row-level security, silently disabling the tenant-isolation policies
  (ADR-0006). Create the app role with `CREATE ROLE app LOGIN NOSUPERUSER;` and grant only
  the needed table privileges. `/ops/ready/` reports `"rls": "degraded"` when the connected
  role can bypass RLS — alert on it.
- Backups/restores must use a `pg_dump`/`pg_restore` whose major version matches the server.
- **PgBouncer / pooling**: RLS tenant scoping sets session-level PostgreSQL GUCs
  (`app.rls_scope`, `app.allowed_org_ids`). Use **session pooling** (or direct connections).
  PgBouncer *transaction* pooling resets GUCs between transactions and breaks tenant
  isolation for web requests.

## Scaling notes

- Run **exactly one** `beat` scheduler. Periodic tasks are additionally guarded by a
  cache-based single-instance lock (`single_instance` in `apps/conversions/tasks.py`), which
  requires Redis to be effective across hosts.
- Login throttling, the upload rate limit, and fine-grained progress require a **shared cache
  (Redis)**; set `FILECONVERTER_USE_REDIS_CACHE=True`. Behind a proxy, set
  `FILECONVERTER_TRUSTED_PROXY_COUNT` so client IPs (and throttles) are per-user, not
  per-proxy.

## Data-subject requests (LAUNCH)

- **Export**: `python manage.py export_user_data <username> --output export.json`.
- **Deletion/erasure**: `python manage.py delete_account <username> --yes` (removes the user
  and any org they solely own, including stored files).
