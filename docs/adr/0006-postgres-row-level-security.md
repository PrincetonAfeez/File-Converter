# 6. PostgreSQL row-level security for tenant isolation

Date: 2026-07-09
Status: Accepted

## Context

Tenant isolation was enforced only at the application layer via `accessible_to` (ADR-0004).
That is tested and effective, but a single future query that forgets to scope would leak
cross-tenant data. We want defense-in-depth at the database layer.

## Decision

- Migration `conversions/0002` enables `ROW LEVEL SECURITY` (with `FORCE`) on the
  customer-data tables `conversions_conversionjob` and `files_fileblob`. Migration
  `conversions/0003` extends the same policy to `audit_auditevent`, `audit_outboxevent`,
  `quotas_quotadecision`, `quotas_usageledger`, and `quotas_usagequota`. Migrations
  `conversions/0004` and `conversions/0005` add `conversions_jobevent` and
  `conversions_conversionbatch` and tighten `WITH CHECK` to mirror `USING`. Each policy:
  reads are visible only when the request opted into scoping (`app.rls_scope='on'`) and the
  row's `organization_id` is in `app.allowed_org_ids`; otherwise the connection is trusted
  (bypass). Write policies use the same tenant expression in `WITH CHECK`.
- `RowLevelSecurityMiddleware` sets those session GUCs from the authenticated user's active
  memberships for the duration of each web request and clears them afterward.
- Non-request contexts (Celery workers, management commands, shell) never set the scope and
  run trusted — this is the intended, safe default (they are trusted system code).
- The migration and middleware are PostgreSQL-only; on SQLite (dev) they are no-ops.

## Consequences

- Cross-tenant **reads** are blocked at the DB even if an application query is unscoped.
- **The application database role must be a non-superuser without `BYPASSRLS`.** PostgreSQL
  superusers ignore RLS entirely; a superuser app role silently disables this control.
  `/ops/ready` reports `"rls": "degraded"` when the connected role can bypass RLS.
- Isolation is verified by a PostgreSQL-only test that scopes to one tenant (as a
  non-superuser role) and confirms other tenants' rows are invisible.
