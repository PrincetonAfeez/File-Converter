# "PostgreSQL RLS isolation tests."
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from apps.conversions.models import ConversionJob, JobEvent
from apps.files.models import FileBlob
from apps.organizations.services import ensure_personal_workspace

pytestmark = pytest.mark.django_db


def _job(username, key):
    user = get_user_model().objects.create_user(username=username, password="pw")
    ws = ensure_personal_workspace(user)
    blob = FileBlob.objects.create(
        organization=ws.organization, workspace=ws, kind=FileBlob.Kind.INPUT, original_name="x.csv"
    )
    blob.file.save("x.csv", SimpleUploadedFile("x.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=user, organization=ws.organization, workspace=ws, source_format="csv",
        target_format="json", converter_name="pandas-table", converter_version="1.0.0",
        input_file=blob, original_display_filename="x.csv", idempotency_key=key,
    )
    JobEvent.objects.create(
        job=job, organization=ws.organization, event_type="job.created", message="seed"
    )
    return ws.organization_id


def test_rls_isolates_tenants_at_db_level():
    if connection.vendor != "postgresql":
        pytest.skip("RLS is a PostgreSQL-only control (no-op on SQLite dev)")

    org_a = _job("rls_tenant_a", "a")
    _job("rls_tenant_b", "b")

    with connection.cursor() as cursor:
        try:
            # Act as a NON-superuser role (as production must) so RLS is not bypassed.
            cursor.execute("DROP ROLE IF EXISTS fc_rls_test;")
            cursor.execute("CREATE ROLE fc_rls_test NOSUPERUSER;")
            cursor.execute("GRANT USAGE ON SCHEMA public TO fc_rls_test;")
            cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO fc_rls_test;")
            cursor.execute("SET ROLE fc_rls_test;")

            # Trusted (no scope) sees both tenants.
            cursor.execute("SELECT set_config('app.rls_scope','off',false);")
            cursor.execute("SELECT count(*) FROM conversions_conversionjob;")
            assert cursor.fetchone()[0] == 2

            # Scoped to tenant A -> only A's rows are visible, at the DB layer.
            cursor.execute("SELECT set_config('app.rls_scope','on',false);")
            cursor.execute("SELECT set_config('app.allowed_org_ids', %s, false);", [str(org_a)])
            cursor.execute("SELECT count(*) FROM conversions_conversionjob;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT count(*) FROM files_fileblob;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT count(*) FROM conversions_jobevent;")
            assert cursor.fetchone()[0] == 1
        finally:
            cursor.execute("RESET ROLE;")
            cursor.execute("SELECT set_config('app.rls_scope','off',false);")
            cursor.execute("SELECT set_config('app.allowed_org_ids','',false);")
            # A role holding granted privileges can't be dropped until they're released.
            cursor.execute("DROP OWNED BY fc_rls_test;")
            cursor.execute("DROP ROLE IF EXISTS fc_rls_test;")
