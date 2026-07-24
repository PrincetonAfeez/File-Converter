# "Shared pytest fixtures for the test suite."
"""Shared pytest fixtures for the File Converter test suite."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.conversions.models import ConversionJob
from apps.files.models import FileBlob
from apps.organizations.models import Membership, Organization, Workspace
from apps.organizations.services import ensure_personal_workspace


@pytest.fixture
def csv_bytes():
    return b"name,value\nalpha,1\n"


@pytest.fixture
def csv_upload(csv_bytes):
    return SimpleUploadedFile("sample.csv", csv_bytes, content_type="text/csv")


@pytest.fixture
def make_user(db):
    def _make(username: str, *, password: str = "pw"):
        return get_user_model().objects.create_user(username=username, password=password)

    return _make


@pytest.fixture
def make_workspace(db, make_user):
    def _make(username: str = "ws-user"):
        user = make_user(username)
        return ensure_personal_workspace(user), user

    return _make


@pytest.fixture
def make_job(db, make_workspace, csv_bytes):
    def _make(
        username: str = "job-user",
        *,
        status=ConversionJob.Status.PENDING,
        claim_generation: int = 0,
        idempotency_key: str | None = None,
        target_format: str = "json",
    ):
        workspace, user = make_workspace(username)
        blob = FileBlob.objects.create(
            organization=workspace.organization,
            workspace=workspace,
            kind=FileBlob.Kind.INPUT,
            original_name="sample.csv",
            byte_size=len(csv_bytes),
        )
        blob.file.save("sample.csv", SimpleUploadedFile("sample.csv", csv_bytes), save=True)
        job = ConversionJob.objects.create(
            owner=user,
            organization=workspace.organization,
            workspace=workspace,
            source_format="csv",
            target_format=target_format,
            status=status,
            claim_generation=claim_generation,
            converter_name="pandas-table",
            converter_version="1.0.0",
            input_file=blob,
            original_display_filename="sample.csv",
            idempotency_key=idempotency_key or username,
            input_byte_size=len(csv_bytes),
        )
        return job, user, workspace

    return _make


@pytest.fixture
def shared_org(db, make_user, csv_bytes):
    """Two-member organization with a General workspace."""
    owner = make_user("shared-owner")
    member = make_user("shared-member")
    org = Organization.objects.create(name="Shared Org")
    ws = Workspace.objects.create(organization=org, name="General")
    Membership.objects.create(user=owner, organization=org, role=Membership.Role.OWNER)
    Membership.objects.create(user=member, organization=org, role=Membership.Role.MEMBER)
    return org, ws, owner, member


@pytest.fixture
def make_shared_job(shared_org, csv_bytes):
    org, ws, _owner, _member = shared_org

    def _make(
        owner,
        *,
        idempotency_key: str,
        status=ConversionJob.Status.PROCESSING,
    ):
        blob = FileBlob.objects.create(
            organization=org,
            workspace=ws,
            kind=FileBlob.Kind.INPUT,
            original_name="sample.csv",
            byte_size=len(csv_bytes),
        )
        return ConversionJob.objects.create(
            owner=owner,
            organization=org,
            workspace=ws,
            source_format="csv",
            target_format="json",
            status=status,
            converter_name="pandas-table",
            converter_version="1.0.0",
            input_file=blob,
            original_display_filename="x.csv",
            idempotency_key=idempotency_key,
        )

    return _make
