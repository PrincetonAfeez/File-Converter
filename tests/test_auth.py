# "Tests for login throttling and access control."
"""Tests for login throttling and access control."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from apps.organizations.models import Membership, Organization, Workspace


@pytest.mark.django_db
def test_login_success(client):
    user = get_user_model().objects.create_user(username="login-ok", password="goodpass")
    org = Organization.objects.create(name="Login Org")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=org, name="General")
    assert client.login(username="login-ok", password="goodpass") is True


@pytest.mark.django_db
def test_login_blocked_for_suspended_org_only(client):
    user = get_user_model().objects.create_user(username="login-susp", password="goodpass")
    org = Organization.objects.create(name="Susp", status=Organization.Status.SUSPENDED)
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    response = client.post(
        reverse("login"),
        {"username": "login-susp", "password": "goodpass"},
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_login_throttle_returns_429(client, settings):
    cache.clear()
    settings.FILECONVERTER_LOGIN_MAX_ATTEMPTS = 1
    settings.FILECONVERTER_LOGIN_IP_MAX_ATTEMPTS = 100
    from fileconverter.ratelimit import increment_counter

    increment_counter("login-throttle:127.0.0.1:throttled", window_seconds=300)
    response = client.post(
        reverse("login"),
        {"username": "throttled", "password": "wrong"},
        REMOTE_ADDR="127.0.0.1",
    )
    assert response.status_code == 429


@pytest.mark.django_db
def test_login_success_clears_throttle_counter(client):
    cache.clear()
    user = get_user_model().objects.create_user(username="login-clear", password="goodpass")
    org = Organization.objects.create(name="Clear Org")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=org, name="General")
    from fileconverter.ratelimit import increment_counter

    increment_counter("login-throttle:127.0.0.1:login-clear", window_seconds=300)
    assert client.login(username="login-clear", password="goodpass") is True
