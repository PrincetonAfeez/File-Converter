# "Database migration 0002: row level security."
from django.db import migrations

# Tables that receive a DB-level tenant-isolation policy (defense-in-depth, ADR-0004/0006).
SCOPED_TABLES = ["conversions_conversionjob", "files_fileblob"]

# Policy: the connection is trusted unless a request opted into scoping (app.rls_scope='on'),
# in which case only rows whose organization_id is in app.allowed_org_ids are visible.
_POLICY_USING = (
    "current_setting('app.rls_scope', true) IS DISTINCT FROM 'on' "
    "OR organization_id::text = ANY("
    "string_to_array(current_setting('app.allowed_org_ids', true), ','))"
)


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in SCOPED_TABLES:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;')
            cursor.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;')
            # USING scopes reads (SELECT/UPDATE/DELETE visibility); WITH CHECK (true) leaves
            # writes unrestricted so legitimate inserts/updates (org_id set correctly by the
            # app) never fail — the isolation goal is preventing cross-tenant READS.
            cursor.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING ({_POLICY_USING}) WITH CHECK (true);"
            )


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in SCOPED_TABLES:
            cursor.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}";')
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [
        ("conversions", "0001_initial"),
        ("files", "0001_initial"),
    ]

    operations = [migrations.RunPython(enable_rls, disable_rls)]
