# "PostgreSQL row-level security scope helpers."
from __future__ import annotations

from django.db import connection

# Tables carrying customer data that get a DB-level tenant-isolation policy. This is
# defense-in-depth UNDER the application-layer `accessible_to` scoping (ADR-0004): even a
# view that forgot to scope cannot read another tenant's rows on PostgreSQL.
RLS_SCOPED_TABLES = [
    "conversions_conversionjob",
    "conversions_conversionbatch",
    "conversions_jobevent",
    "files_fileblob",
    "audit_auditevent",
    "audit_outboxevent",
    "quotas_quotadecision",
    "quotas_usageledger",
    "quotas_usagequota",
]

# Model: the connection is TRUSTED by default (scope unset -> policy bypassed), so Celery
# workers, management commands, and the shell see all rows. A web request OPTS IN to
# scoping for its duration via the middleware, then clears it.


def is_postgres() -> bool:
    return connection.vendor == "postgresql"


def apply_request_scope(org_ids) -> None:
    """Restrict the current connection to the given organization ids for this request."""
    if not is_postgres():
        return
    csv = ",".join(str(oid) for oid in org_ids)
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.rls_scope', 'on', false)")
        cursor.execute("SELECT set_config('app.allowed_org_ids', %s, false)", [csv])


def clear_scope() -> None:
    """Return the connection to the trusted (bypass) default."""
    if not is_postgres():
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.rls_scope', 'off', false)")
        cursor.execute("SELECT set_config('app.allowed_org_ids', '', false)")
