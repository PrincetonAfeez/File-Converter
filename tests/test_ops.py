# "Tests for health, readiness, and flags."
"""Tests for ops endpoints, metrics, and feature flags."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.ops.flags import flag_enabled
from apps.ops.metrics import collect_metrics, job_status_counts
from apps.ops.models import FeatureFlag


@pytest.mark.django_db
def test_health_endpoint(client):
    response = client.get("/ops/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.django_db
def test_readiness_ok(client):
    response = client.get("/ops/ready/")
    assert response.status_code in {200, 503}
    assert "checks" in response.json()


@pytest.mark.django_db
def test_collect_metrics_structure(make_job):
    make_job("metrics-user")
    payload = collect_metrics()
    assert "queue_depth" in payload
    assert "job_status_counts" in payload
    assert isinstance(job_status_counts(), dict)


@pytest.mark.django_db
def test_flag_enabled_db_and_settings(settings):
    from django.core.cache import cache

    cache.clear()
    FeatureFlag.objects.create(name="beta_feature", enabled=True)
    assert flag_enabled("beta_feature", default=False) is True
    settings.FILECONVERTER_FLAGS = {"beta_feature": False}
    assert flag_enabled("beta_feature", default=False) is False


@pytest.mark.django_db
def test_metrics_staff_access(client):
    user = get_user_model().objects.create_user(username="staff-metrics", password="pw")
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    client.force_login(user)
    assert client.get("/ops/metrics/").status_code == 200
