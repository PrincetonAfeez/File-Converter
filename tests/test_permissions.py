# "Tests for organization ACL helpers."
"""Tests for apps.organizations.permissions."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.conversions.models import ConversionJob
from apps.organizations.models import Membership, Organization, Workspace, WorkspaceMembership
from apps.organizations.permissions import (
    get_org_membership,
    user_may_cancel_job,
    user_may_download_job,
    user_may_write_workspace,
)


@pytest.mark.django_db
def test_auditor_cannot_write_or_cancel(shared_org, make_user, make_shared_job):
    org, ws, owner, member = shared_org
    auditor = make_user("auditor-user")
    Membership.objects.create(user=auditor, organization=org, role=Membership.Role.AUDITOR)
    assert user_may_write_workspace(auditor, ws) is False

    job = make_shared_job(member, idempotency_key="aud")
    assert user_may_cancel_job(auditor, job) is False
    assert user_may_download_job(auditor, job) is True


@pytest.mark.django_db
def test_owner_can_cancel_and_download_others_jobs(shared_org, make_shared_job):
    org, ws, owner, member = shared_org
    job = make_shared_job(
        member, idempotency_key="own", status=ConversionJob.Status.DONE
    )
    assert user_may_cancel_job(owner, job) is True
    assert user_may_download_job(owner, job) is True


@pytest.mark.django_db
def test_member_cannot_act_on_other_members_job(shared_org, make_shared_job):
    org, ws, owner, member = shared_org
    other = get_user_model().objects.create_user(username="other-member", password="pw")
    Membership.objects.create(user=other, organization=org, role=Membership.Role.MEMBER)
    job = make_shared_job(
        member, idempotency_key="mem", status=ConversionJob.Status.DONE
    )
    assert user_may_download_job(other, job) is False
    assert user_may_cancel_job(other, job) is False


@pytest.mark.django_db
def test_workspace_acl_auditor_blocked(shared_org, make_user):
    org, ws, _owner, member = shared_org
    ws_member = make_user("ws-auditor")
    Membership.objects.create(user=ws_member, organization=org, role=Membership.Role.MEMBER)
    WorkspaceMembership.objects.create(
        user=ws_member, workspace=ws, role=Membership.Role.AUDITOR
    )
    assert user_may_write_workspace(ws_member, ws) is False


@pytest.mark.django_db
def test_get_org_membership_returns_none_for_anonymous():
    org = Organization.objects.create(name="Anon Org")
    assert get_org_membership(type("U", (), {"is_authenticated": False})(), org) is None
