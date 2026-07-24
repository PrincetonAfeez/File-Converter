# "Tests for RLS request middleware."
"""Tests for PostgreSQL row-level security middleware and scope helpers."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import MiddlewareNotUsed
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory

from apps.organizations.models import Membership, Organization, Workspace
from apps.organizations.rls import apply_request_scope, clear_scope, is_postgres
from fileconverter.middleware import RowLevelSecurityMiddleware


def test_apply_request_scope_is_noop_on_sqlite():
    apply_request_scope([1, 2, 3])
    clear_scope()


def test_row_level_security_middleware_disabled_on_sqlite():
    if is_postgres():
        pytest.skip("Middleware is enabled on PostgreSQL")
    with pytest.raises(MiddlewareNotUsed):
        RowLevelSecurityMiddleware(lambda request: HttpResponse("ok"))


@pytest.mark.django_db
def test_row_level_security_middleware_applies_and_clears_scope():
    if connection.vendor != "postgresql":
        pytest.skip("RowLevelSecurityMiddleware is PostgreSQL-only")

    user = get_user_model().objects.create_user(username="rls-mw", password="pw")
    org = Organization.objects.create(name="RLS MW Org")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=org, name="General")

    captured = {}

    def get_response(request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.rls_scope', true)")
            captured["scope"] = cursor.fetchone()[0]
            cursor.execute("SELECT current_setting('app.allowed_org_ids', true)")
            captured["org_ids"] = cursor.fetchone()[0]
        return HttpResponse("ok")

    request = RequestFactory().get("/dashboard/")
    request.user = user
    response = RowLevelSecurityMiddleware(get_response)(request)

    assert response.status_code == 200
    assert captured["scope"] == "on"
    assert str(org.pk) in captured["org_ids"]

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.rls_scope', true)")
        assert cursor.fetchone()[0] in {None, "", "off"}


@pytest.mark.django_db
def test_row_level_security_middleware_leaves_anonymous_unscoped():
    if connection.vendor != "postgresql":
        pytest.skip("RowLevelSecurityMiddleware is PostgreSQL-only")

    captured = {}

    def get_response(request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.rls_scope', true)")
            captured["scope"] = cursor.fetchone()[0]
        return HttpResponse("ok")

    request = RequestFactory().get("/")
    request.user = type("Anon", (), {"is_authenticated": False})()
    RowLevelSecurityMiddleware(get_response)(request)
    assert captured["scope"] in {None, "", "off"}
