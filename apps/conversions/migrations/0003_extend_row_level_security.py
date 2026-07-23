# "Database migration 0003: extend row level security."
from django.db import migrations

# Additional tenant-scoped tables (defense-in-depth, ADR-0006).
SCOPED_TABLES = [
    "audit_auditevent",
    "audit_outboxevent",
    "quotas_quotadecision",
    "quotas_usageledger",
    "quotas_usagequota",
]

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
        ("conversions", "0002_row_level_security"),
        ("audit", "0001_initial"),
        ("quotas", "0001_initial"),
    ]

    operations = [migrations.RunPython(enable_rls, disable_rls)]
