# On-call rotation (GROWTH)

Define who gets paged when synthetic probes or Prometheus alerts fire. Keep this
file updated when the rotation changes — the audit framework expects a named
escalation path in-repo even if paging is delivered by an external tool.

## Rotation (example — replace with your team)

| Week of (UTC) | Primary on-call        | Secondary / escalation |
|---------------|------------------------|-------------------------|
| 2026-07-14    | engineer-a@example.com | engineer-b@example.com  |
| 2026-07-21    | engineer-b@example.com | engineer-c@example.com  |
| 2026-07-28    | engineer-c@example.com | engineer-a@example.com  |

## Paging channels

1. **Synthetic uptime** — `.github/workflows/uptime-synthetic.yml` (every 15 min) probes
   `STAGING_BASE_URL` via `scripts/check_readiness.py`. Workflow failure notifies
   GitHub watchers; mirror to PagerDuty/Opsgenie with a GitHub integration.
2. **Prometheus** — import `docs/monitoring/prometheus-alerts.yml` into Alertmanager;
   route `severity=page` to the primary on-call above.
3. **Sentry** — set `SENTRY_DSN` in production; assign the same rotation in Sentry
   project alerts for unhandled 5xx spikes.

## Response SLA (matches OPERATIONS.md)

- **Acknowledge** alert within 15 minutes during business hours; 30 minutes off-hours.
- **Mitigate** using `/ops/ready/` triage, deploy rollback, worker scale-up (see OPERATIONS.md).
- **Communicate** on the customer status page when user-visible impact exceeds 5 minutes.

## Handoff checklist

- [ ] Primary has GitHub + PagerDuty access
- [ ] Repo secret `STAGING_BASE_URL` set (uptime workflow fails closed if missing)
- [ ] `SENTRY_DSN` set on web + worker; `python manage.py check --deploy` clean
- [ ] `python scripts/validate_deploy_env.py --require-uptime-url` passes in the deploy env
- [ ] Last backup/restore drill date recorded in OPERATIONS.md change log

## Required secrets / env (enforced)

| Variable | Enforced by |
|----------|-------------|
| `STAGING_BASE_URL` | `.github/workflows/uptime-synthetic.yml` → `validate_deploy_env.py --require-uptime-url` |
| `SENTRY_DSN` | `fileconverter.E001` (`check --deploy`), `/ops/ready/` `sentry` check, `validate_deploy_env.py` |
