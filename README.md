# File Converter

A Django + HTMX Multi-Format File Converter with Background Jobs

What is implemented:

- Authenticated dashboard with upload, job history, job detail, HTMX polling, cancel, and download.
- Organization/workspace membership model with a personal workspace bootstrap.
- Tenant-scoped `FileBlob`, quota decisions, usage ledger, audit events, and outbox events.
- Converter registry keyed by `(source_format, target_format)`.
- Working Pillow image conversions and pandas table conversions.
- LibreOffice document-to-PDF and FFmpeg media adapters behind the same interface.
- Celery task runner with late-ack settings, atomic claim, `claim_generation` fencing, heartbeat, cooperative cancel flag, and fenced output promotion.
- Automatic retry of transient failures (bounded by `FILECONVERTER_MAX_ATTEMPTS`) and a Celery-beat reaper that requeues jobs abandoned by dead workers.
- Retention enforcement: a beat task purges outputs past their TTL and marks the job `expired`.
- Content-type sniffing (libmagic) that rejects uploads whose bytes contradict their extension; only successful conversions are billed to the usage ledger.
- Local filesystem storage with paths shaped like tenant/workspace object-storage keys.

## Production notes

- `DEBUG` defaults to `False`; you must supply a strong `DJANGO_SECRET_KEY` or the app refuses to boot.
- The Docker image runs `collectstatic` at build time and runs migrations on the `web` container at start (`RUN_MIGRATIONS=1`).
- Run a Celery **beat** process alongside workers so stale-job reaping and output expiry fire on schedule.
- See **[OPERATIONS.md](OPERATIONS.md)** for backups/restore, rollback, monitoring, incident
  response, scaling, and data-subject (export/deletion) procedures.
- **[docs/SLO.md](docs/SLO.md)** (targets/error budgets), **[docs/adr/](docs/adr/)**
  (architecture decisions), **[loadtest/](loadtest/)** (k6 capacity test). CI runs the suite
  on SQLite *and* PostgreSQL and executes a backup/restore drill on every push.
- Metrics for scraping at `GET /ops/metrics/` (queue depth, job counts, dead-letters);
  feature flags via the `apps.ops.FeatureFlag` admin + `flag_enabled()`.
- Errors: set `SENTRY_DSN` in production (`sentry-sdk` in `requirements.lock`); required when
  `DJANGO_DEBUG=False` (`check --deploy` + `/ops/ready/`). Initialized on web/worker boot.
  Every log line carries `req=<id>` / `X-Request-ID`.
- Synthetic uptime: `scripts/check_readiness.py` + `.github/workflows/uptime-synthetic.yml`
  (fails if repo secret `STAGING_BASE_URL` is missing). Validate with
  `scripts/validate_deploy_env.py`. Alert rules in `docs/monitoring/prometheus-alerts.yml`;
  on-call rotation in `docs/monitoring/oncall.md`.

### Accepted limitations (out of scope)

- No self-serve signup — accounts are provisioned via the admin or `bootstrap_demo`.
- No MFA on login (login is throttled; brute-force protection is present).
- No transactional email / password-reset flow.
- Local-filesystem storage; S3/MinIO promotion is future work.
- Webhook delivery is **at-least-once** — consumers must dedupe on `X-Idempotency-Key`.
- Best-effort SLA; not SOC 2 / HIPAA certified. GDPR-oriented export/deletion commands exist
  but do not constitute full regulatory compliance.
- Row-lock-dependent controls (quota, throttles, progress) require PostgreSQL + Redis; they
  degrade to best-effort on the SQLite + LocMemCache dev defaults.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
$env:CELERY_TASK_ALWAYS_EAGER="True"
python manage.py migrate
python manage.py bootstrap_demo
python manage.py runserver
```

Sign in at `http://127.0.0.1:8000/accounts/login/` with `demo` / `demo12345`.

For real background workers, run Redis and start a worker:

```powershell
docker compose up redis
celery -A fileconverter worker -l info -Q default
celery -A fileconverter beat -l info
python manage.py runserver
```

## Notes

This is a production-oriented foundation, not the full enterprise surface from the spec. The hard parts are intentionally represented in code early: converter isolation boundaries, state transitions, exactly-once effects via fencing, and progress/status separation. The remaining product areas to deepen are MinIO/S3 promotion, deep malware scanning (beyond content-type sniffing), support console workflows, metrics/tracing, full batch ZIP streaming, and hardened runtime sandbox enforcement.
