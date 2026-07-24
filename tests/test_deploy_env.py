# "Tests for deploy env validation and Sentry checks."
"""Tests for production deploy env validation and system checks."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from django.core.checks import Error
from django.test import override_settings

from apps.ops.checks import check_sentry_dsn_for_deploy


def test_check_sentry_passes_when_debug():
    with override_settings(DEBUG=True, FILECONVERTER_REQUIRE_SENTRY=True):
        assert check_sentry_dsn_for_deploy(None) == []


def test_check_sentry_passes_when_opted_out():
    with override_settings(DEBUG=False, FILECONVERTER_REQUIRE_SENTRY=False):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SENTRY_DSN", None)
            assert check_sentry_dsn_for_deploy(None) == []


def test_check_sentry_errors_when_missing_in_production():
    with override_settings(DEBUG=False, FILECONVERTER_REQUIRE_SENTRY=True):
        with patch.dict(os.environ, {"SENTRY_DSN": ""}, clear=False):
            errors = check_sentry_dsn_for_deploy(None)
    assert len(errors) == 1
    assert isinstance(errors[0], Error)
    assert errors[0].id == "fileconverter.E001"


def test_check_sentry_passes_when_dsn_set():
    with override_settings(DEBUG=False, FILECONVERTER_REQUIRE_SENTRY=True):
        with patch.dict(os.environ, {"SENTRY_DSN": "https://public@example.invalid/1"}):
            assert check_sentry_dsn_for_deploy(None) == []


def test_validate_deploy_env_helper():
    from apps.ops.deploy_env import validate_deploy_env

    with patch.dict(os.environ, {"DJANGO_DEBUG": "False"}, clear=False):
        os.environ.pop("SENTRY_DSN", None)
        errors = validate_deploy_env(require_uptime_url=False)
    assert any("SENTRY_DSN" in e for e in errors)

    with patch.dict(
        os.environ,
        {"DJANGO_DEBUG": "False", "SENTRY_DSN": "https://x@y.invalid/1"},
        clear=False,
    ):
        os.environ.pop("STAGING_BASE_URL", None)
        errors = validate_deploy_env(require_uptime_url=True)
    assert any("STAGING_BASE_URL" in e for e in errors)

    with patch.dict(
        os.environ,
        {
            "DJANGO_DEBUG": "False",
            "SENTRY_DSN": "https://x@y.invalid/1",
            "STAGING_BASE_URL": "https://staging.example.com",
        },
    ):
        assert validate_deploy_env(require_uptime_url=True) == []


@pytest.mark.django_db
def test_readiness_includes_sentry_check(client, settings):
    settings.DEBUG = True
    settings.FILECONVERTER_REQUIRE_SENTRY = False
    response = client.get("/ops/ready/")
    assert "sentry" in response.json()["checks"]
    assert response.json()["checks"]["sentry"] == "ok"


@pytest.mark.django_db
def test_readiness_sentry_degraded_when_required_and_missing(client, settings):
    settings.DEBUG = False
    settings.FILECONVERTER_REQUIRE_SENTRY = True
    with patch("fileconverter.observability.sentry_initialized", return_value=False):
        response = client.get("/ops/ready/")
    assert response.status_code == 503
    assert response.json()["checks"]["sentry"] == "degraded"
