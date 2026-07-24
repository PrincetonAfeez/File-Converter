# "Tests for management commands."
"""Tests for management commands."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.conversions.models import ConversionJob
from apps.organizations.models import Membership, Organization


@pytest.mark.django_db
def test_bootstrap_demo_creates_user():
    out = StringIO()
    call_command("bootstrap_demo", "--username", "demo-cli", stdout=out)
    user = get_user_model().objects.get(username="demo-cli")
    assert Membership.objects.filter(user=user).exists()
    assert "demo-cli" in out.getvalue()


@pytest.mark.django_db
def test_export_user_data_command_stdout(make_job):
    _job, user, _ws = make_job("export-cmd")
    out = StringIO()
    call_command("export_user_data", user.username, stdout=out)
    payload = json.loads(out.getvalue())
    assert payload["user"]["username"] == user.username


@pytest.mark.django_db
def test_export_user_data_command_missing_user():
    with pytest.raises(CommandError, match="No user"):
        call_command("export_user_data", "missing-user")


@pytest.mark.django_db
def test_delete_account_requires_yes(make_user):
    user = make_user("delete-me")
    with pytest.raises(CommandError, match="without --yes"):
        call_command("delete_account", user.username)


@pytest.mark.django_db
def test_delete_account_with_yes(make_user):
    user = make_user("delete-yes")
    out = StringIO()
    call_command("delete_account", user.username, "--yes", stdout=out)
    assert not get_user_model().objects.filter(username="delete-yes").exists()


@pytest.mark.django_db
def test_convert_pending_once_processes_queue(settings, make_job):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    out = StringIO()
    call_command("convert_pending_once", stdout=out)
    job.refresh_from_db()
    assert job.status in {ConversionJob.Status.DONE, ConversionJob.Status.FAILED}
    assert "Processed 1 pending job" in out.getvalue()
