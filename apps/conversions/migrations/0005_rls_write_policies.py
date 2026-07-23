# "Database migration 0005: rls write policies."
from django.db import migrations

# All tenant-scoped tables (ADR-0006), including JobEvent and ConversionBatch.
SCOPED_TABLES = [
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

_POLICY_USING = (
    "current_setting('app.rls_scope', true) IS DISTINCT FROM 'on' "
    "OR organization_id::text = ANY("
    "string_to_array(current_setting('app.allowed_org_ids', true), ','))"
)


def _recreate_policies(cursor, *, with_check: str) -> None:
    for table in SCOPED_TABLES:
        cursor.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}";')
        if table in {"conversions_jobevent", "conversions_conversionbatch"}:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;')
            cursor.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;')
        cursor.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f"USING ({_POLICY_USING}) WITH CHECK ({with_check});"
        )


def tighten_write_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _recreate_policies(cursor, with_check=_POLICY_USING)


def restore_permissive_writes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _recreate_policies(cursor, with_check="true")


class Migration(migrations.Migration):
    dependencies = [
        ("conversions", "0004_jobevent_organization"),
    ]

    operations = [migrations.RunPython(tighten_write_policies, restore_permissive_writes)]
