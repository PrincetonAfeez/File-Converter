# 4. Tenant scoping strategy

Date: 2026-07-09
Status: Accepted

## Context

The product is multi-tenant (organization → workspace → user). Relying on each view to
remember a tenant filter is fragile: one missed filter is a cross-tenant data leak.

## Decision

- Tenancy is enforced at the query layer via `ConversionJob.objects.accessible_to(user)`,
  which restricts to organizations where the user has an active membership.
- All customer-facing job views use it; `get_job_for_user` scopes first (a foreign job is a
  404, no existence disclosure) then applies the workspace-level ACL.
- Cross-tenant isolation is covered by tests (`test_hardening.py`).

## Consequences

- Cross-org access is blocked by a reusable, tested pattern rather than per-view discipline.
- PostgreSQL Row-Level Security (ADR-0006) adds defense-in-depth on tenant-scoped tables;
  web requests opt in via `RowLevelSecurityMiddleware`, while workers and management
  commands run in the trusted (bypass) mode by design.
