# "Tests for project middleware."
"""Tests for fileconverter middleware."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory

from fileconverter.middleware import (
    ContentSecurityPolicyMiddleware,
    RequestIDMiddleware,
    SlowQueryMiddleware,
    get_request_id,
)


def test_request_id_middleware_sets_header():
    def get_response(request):
        return HttpResponse("ok")

    middleware = RequestIDMiddleware(get_response)
    request = RequestFactory().get("/")
    response = middleware(request)
    assert response["X-Request-ID"]
    assert hasattr(request, "request_id")
    assert get_request_id() == "-"


def test_csp_middleware_adds_header(settings):
    settings.FILECONVERTER_CSP = "default-src 'self'"
    middleware = ContentSecurityPolicyMiddleware(lambda r: HttpResponse("ok"))
    response = middleware(RequestFactory().get("/"))
    assert "default-src 'self'" in response["Content-Security-Policy"]


def test_slow_query_middleware_enabled_in_production_defaults(settings):
    settings.FILECONVERTER_SLOW_QUERY_MS = 500
    middleware = SlowQueryMiddleware(lambda r: HttpResponse("ok"))
    assert middleware.threshold_ms == 500


@pytest.mark.django_db
def test_login_access_middleware_logs_out_disabled_user(client):
    from apps.organizations.models import Membership, Organization, Workspace

    user = get_user_model().objects.create_user(username="mw-disabled", password="pw")
    org = Organization.objects.create(name="MW Org")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=org, name="General")
    client.force_login(user)
    Membership.objects.filter(user=user).update(status=Membership.Status.DISABLED)
    response = client.get("/dashboard/")
    assert response.status_code == 302
    assert "/login/" in response["Location"]
