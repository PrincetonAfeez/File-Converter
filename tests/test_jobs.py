# "Tests for job claim fencing."
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.conversions.models import ConversionJob
from apps.conversions.services import claim_job, terminal_transition
from apps.files.models import FileBlob
from apps.organizations.services import ensure_personal_workspace


@pytest.mark.django_db
def test_claim_increments_generation_and_blocks_second_claim(settings):
    user = get_user_model().objects.create_user(username="u", password="pw")
    workspace = ensure_personal_workspace(user)
    blob = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.INPUT,
        original_name="input.csv",
        byte_size=3,
    )
    blob.file.save("input.csv", SimpleUploadedFile("input.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=user,
        organization=workspace.organization,
        workspace=workspace,
        source_format="csv",
        target_format="json",
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="input.csv",
        idempotency_key="abc",
    )

    claimed = claim_job(job.pk, worker_id="worker-1")
    assert claimed is not None
    claimed_job, token = claimed

    assert claimed_job.status == ConversionJob.Status.PROCESSING
    assert token == 1
    assert claim_job(job.pk, worker_id="worker-2") is None


@pytest.mark.django_db
def test_stale_terminal_write_is_fenced():
    user = get_user_model().objects.create_user(username="u2", password="pw")
    workspace = ensure_personal_workspace(user)
    blob = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.INPUT,
        original_name="input.csv",
        byte_size=3,
    )
    blob.file.save("input.csv", SimpleUploadedFile("input.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=user,
        organization=workspace.organization,
        workspace=workspace,
        source_format="csv",
        target_format="json",
        status=ConversionJob.Status.PROCESSING,
        claim_generation=2,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="input.csv",
        idempotency_key="def",
    )

    assert terminal_transition(job, 1, ConversionJob.Status.DONE, message="stale") is False
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.PROCESSING
