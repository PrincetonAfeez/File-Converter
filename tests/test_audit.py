# "Tests for audit events and outbox delivery."
"""Tests for audit services."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from apps.audit.models import AuditEvent, OutboxEvent
from apps.audit.services import (
    deliver_outbox_events,
    enqueue_outbox,
    purge_delivered_outbox_events,
    purge_failed_outbox_events,
    write_audit,
)
from apps.organizations.models import Organization


@pytest.mark.django_db
def test_write_audit_persists_row(make_user):
    user = make_user("audit-user")
    org = Organization.objects.create(name="Audit Org")
    event = write_audit(
        organization=org,
        actor=user,
        event_type="test.event",
        message="hello",
    )
    assert AuditEvent.objects.filter(pk=event.pk, event_type="test.event").exists()


@pytest.mark.django_db
def test_enqueue_outbox_is_idempotent():
    org = Organization.objects.create(name="Outbox Org")
    first = enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="idem-1",
        payload={"x": 1},
    )
    second = enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="idem-1",
        payload={"x": 1},
    )
    assert first.pk == second.pk
    assert OutboxEvent.objects.filter(idempotency_key="idem-1").count() == 1


@pytest.mark.django_db
def test_deliver_outbox_success(settings):
    org = Organization.objects.create(name="Deliver Org")
    event = enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="deliver-ok",
        payload={"ok": True},
    )
    settings.FILECONVERTER_WEBHOOK_URL = "https://hooks.example/ingest"
    settings.FILECONVERTER_WEBHOOK_SECRET = "secret"

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("apps.audit.services._OPENER.open", return_value=mock_response):
        delivered = deliver_outbox_events()

    assert delivered == 1
    event.refresh_from_db()
    assert event.delivered_at is not None


@pytest.mark.django_db
def test_deliver_outbox_rejects_non_http(settings):
    settings.FILECONVERTER_WEBHOOK_URL = "ftp://bad.example/x"
    org = Organization.objects.create(name="Bad URL Org")
    enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="bad-url",
        payload={},
    )
    assert deliver_outbox_events() == 0


@pytest.mark.django_db
def test_purge_outbox_events():
    org = Organization.objects.create(name="Purge Org")
    from django.utils import timezone

    old = enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="purge-old",
        payload={},
    )
    OutboxEvent.objects.filter(pk=old.pk).update(
        delivered_at=timezone.now() - timezone.timedelta(days=60)
    )
    failed = enqueue_outbox(
        organization=org,
        event_type="conversion.job.failed",
        idempotency_key="purge-fail",
        payload={},
    )
    OutboxEvent.objects.filter(pk=failed.pk).update(
        failed_at=timezone.now() - timezone.timedelta(days=60)
    )
    assert purge_delivered_outbox_events(older_than_days=30) >= 1
    assert purge_failed_outbox_events(older_than_days=30) >= 1


@pytest.mark.django_db
def test_deliver_one_includes_signature_header(settings):
    from apps.audit.services import _deliver_one

    org = Organization.objects.create(name="Sig Org")
    event = enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="sig-1",
        payload={"a": 1},
    )
    settings.FILECONVERTER_WEBHOOK_SECRET = "topsecret"
    captured = {}

    def fake_open(request, timeout):
        captured["headers"] = dict(request.header_items())
        mock = MagicMock()
        mock.status = 204
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    with patch("apps.audit.services._OPENER.open", side_effect=fake_open):
        _deliver_one(event, "https://hooks.example/ingest", 5)

    assert "X-signature" in {k.lower(): v for k, v in captured["headers"].items()} or any(
        h.lower() == "x-signature" for h in captured["headers"]
    )
