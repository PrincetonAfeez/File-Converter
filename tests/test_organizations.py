# "Tests for org services and GDPR flows."
"""Tests for organization services and lifecycle."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.conversions.models import ConversionJob
from apps.files.models import FileBlob
from apps.organizations.lifecycle import delete_user_account, export_user_data
from apps.organizations.models import Membership, Organization, Workspace
from apps.organizations.services import (
    accessible_workspaces,
    ensure_personal_workspace,
    get_active_workspace,
    organization_is_active,
    output_ttl_hours,
    set_active_workspace,
    user_has_login_access,
)


@pytest.mark.django_db
def test_organization_is_active():
    active = Organization.objects.create(name="Active Org")
    suspended = Organization.objects.create(name="Susp Org", status=Organization.Status.SUSPENDED)
    assert organization_is_active(active) is True
    assert organization_is_active(suspended) is False


@pytest.mark.django_db
def test_output_ttl_hours_honors_zero():
    org = Organization.objects.create(name="TTL Org", default_output_ttl_hours=0)
    assert output_ttl_hours(org) == 0


@pytest.mark.django_db
def test_ensure_personal_workspace_does_not_bootstrap_disabled_user(make_user):
    user = make_user("disabled-bootstrap")
    org = Organization.objects.create(name="Old Org")
    Membership.objects.create(
        user=user, organization=org, status=Membership.Status.DISABLED, role=Membership.Role.MEMBER
    )
    assert ensure_personal_workspace(user) is None


@pytest.mark.django_db
def test_user_has_login_access_requires_active_org(make_user):
    user = make_user("login-access")
    org = Organization.objects.create(name="Suspended", status=Organization.Status.SUSPENDED)
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    assert user_has_login_access(user) is False


@pytest.mark.django_db
def test_accessible_workspaces_excludes_suspended(make_user):
    user = make_user("ws-access")
    active = Organization.objects.create(name="Active", status=Organization.Status.ACTIVE)
    suspended = Organization.objects.create(name="Susp", status=Organization.Status.SUSPENDED)
    Membership.objects.create(user=user, organization=active, role=Membership.Role.MEMBER)
    Membership.objects.create(user=user, organization=suspended, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=active, name="Good")
    Workspace.objects.create(organization=suspended, name="Bad")
    names = [ws.name for ws in accessible_workspaces(user)]
    assert "Good" in names
    assert "Bad" not in names


@pytest.mark.django_db
def test_set_active_workspace_rejects_suspended(rf, make_user):
    user = make_user("set-ws")
    org = Organization.objects.create(name="Susp", status=Organization.Status.SUSPENDED)
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    ws = Workspace.objects.create(organization=org, name="Main")
    request = rf.get("/")
    request.user = user
    request.session = {}
    assert set_active_workspace(request, str(ws.public_id)) is None


@pytest.mark.django_db
def test_get_active_workspace_persists_session(rf, make_user):
    user = make_user("get-ws")
    ws = ensure_personal_workspace(user)
    request = rf.get("/")
    request.user = user
    request.session = {}
    active = get_active_workspace(request)
    assert active.pk == ws.pk
    assert request.session["active_workspace_id"] == str(ws.public_id)


@pytest.mark.django_db
def test_export_user_data_includes_extended_fields(make_job):
    job, user, _ws = make_job("export-ext")
    data = export_user_data(user)
    assert "job_events" in data
    assert "workspace_memberships" in data
    assert "usage_quotas" in data
    assert data["export_notes"]["binary_payloads_excluded"] is True


@pytest.mark.django_db
def test_delete_user_account_scrubs_shared_org(make_user):
    owner = make_user("shared-owner")
    member = make_user("shared-member")
    org = Organization.objects.create(name="Shared Delete Org")
    ws = Workspace.objects.create(organization=org, name="General")
    Membership.objects.create(user=owner, organization=org, role=Membership.Role.OWNER)
    Membership.objects.create(user=member, organization=org, role=Membership.Role.MEMBER)
    blob = FileBlob.objects.create(
        organization=org, workspace=ws, kind=FileBlob.Kind.INPUT, original_name="in.csv"
    )
    ConversionJob.objects.create(
        owner=member,
        organization=org,
        workspace=ws,
        source_format="csv",
        target_format="json",
        status=ConversionJob.Status.DONE,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="secret.csv",
        idempotency_key="scrub",
    )
    delete_user_account(member)
    job = ConversionJob.objects.get(idempotency_key="scrub")
    assert job.original_display_filename == "deleted"
