# "Tests for quota admission control."
"""Tests for quota admission and ledger."""

from __future__ import annotations

import pytest

from apps.conversions.models import ConversionJob
from apps.quotas.models import QuotaDecision, UsageQuota
from apps.quotas.services import record_quota_denial, release_or_record_usage, reserve_quota


@pytest.mark.django_db
def test_reserve_quota_denies_oversized_upload(make_workspace, settings):
    workspace, user = make_workspace("quota-big")
    settings.FILECONVERTER_MAX_UPLOAD_BYTES = 100
    decision = reserve_quota(user=user, workspace=workspace, requested_bytes=500)
    assert decision.result == QuotaDecision.Result.DENIED
    assert decision.reason == "upload_too_large"


@pytest.mark.django_db
def test_reserve_quota_denies_too_many_active_jobs(make_workspace):
    workspace, user = make_workspace("quota-active")
    UsageQuota.objects.update_or_create(
        organization=workspace.organization,
        workspace=workspace,
        defaults={"max_active_jobs": 0},
    )
    decision = reserve_quota(user=user, workspace=workspace, requested_bytes=10)
    assert decision.result == QuotaDecision.Result.DENIED
    assert decision.reason == "too_many_active_jobs"


@pytest.mark.django_db
def test_reserve_quota_allows_when_under_limits(make_workspace):
    workspace, user = make_workspace("quota-ok")
    decision = reserve_quota(user=user, workspace=workspace, requested_bytes=10)
    assert decision.result == QuotaDecision.Result.ALLOWED


@pytest.mark.django_db
def test_record_quota_denial_persists(make_workspace):
    workspace, user = make_workspace("quota-deny")
    decision = record_quota_denial(
        user=user, workspace=workspace, requested_bytes=10, reason="manual"
    )
    assert QuotaDecision.objects.filter(pk=decision.pk, reason="manual").exists()


@pytest.mark.django_db
def test_release_or_record_usage_is_noop(make_job):
    job, _user, _ws = make_job()
    assert release_or_record_usage(job=job) is None
