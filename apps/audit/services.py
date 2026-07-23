# "Audit write helpers and outbox delivery."
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from .models import AuditEvent, OutboxEvent

logger = logging.getLogger(__name__)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Treat any redirect as a delivery failure rather than silently following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirect not allowed: {newurl}", headers, fp
        )


_OPENER = urllib.request.build_opener(_NoRedirect)


def write_audit(
    *,
    organization,
    workspace=None,
    actor=None,
    event_type: str,
    obj=None,
    message: str = "",
    metadata=None,
):
    return AuditEvent.objects.create(
        organization=organization,
        workspace=workspace,
        actor=actor,
        event_type=event_type,
        object_type=obj.__class__.__name__ if obj else "",
        object_id=str(getattr(obj, "public_id", "")) if obj else "",
        message=message,
        metadata=metadata or {},
    )


def enqueue_outbox(
    *, organization=None, organization_id=None, event_type: str, idempotency_key: str, payload: dict
):
    if organization_id is None:
        organization_id = organization.pk
    return OutboxEvent.objects.get_or_create(
        organization_id=organization_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        defaults={"payload": payload},
    )[0]


def _deliver_one(event: OutboxEvent, webhook_url: str, timeout: float) -> None:
    if urlparse(webhook_url).scheme not in {"http", "https"}:
        raise ValueError(f"Refusing to deliver to non-HTTP(S) webhook URL: {webhook_url!r}")
    body = json.dumps(
        {
            "id": str(event.public_id),
            "event_type": event.event_type,
            "idempotency_key": event.idempotency_key,
            "payload": event.payload,
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Event-Type": event.event_type,
        "X-Idempotency-Key": event.idempotency_key,
    }
    secret = settings.FILECONVERTER_WEBHOOK_SECRET
    if secret:
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Signature"] = f"sha256={signature}"
    request = urllib.request.Request(webhook_url, data=body, headers=headers, method="POST")
    with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310 - scheme validated above
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise urllib.error.HTTPError(webhook_url, status, "non-2xx", response.headers, None)


def deliver_outbox_events(*, batch_size: int = 100) -> int:
    """Deliver undelivered OutboxEvents to the configured webhook with capped retries.

    No-op when no webhook is configured (events stay pending until one is). Returns the
    number of events delivered successfully this pass.
    """
    from apps.ops.flags import flag_enabled

    if not flag_enabled("webhook_delivery", default=True):
        return 0

    webhook_url = settings.FILECONVERTER_WEBHOOK_URL
    if not webhook_url:
        return 0
    max_attempts = settings.FILECONVERTER_OUTBOX_MAX_ATTEMPTS
    timeout = settings.FILECONVERTER_WEBHOOK_TIMEOUT_SECONDS
    delivered = 0
    while delivered < batch_size:
        with transaction.atomic():
            pending = OutboxEvent.objects.filter(
                delivered_at__isnull=True,
                failed_at__isnull=True,
                attempts__lt=max_attempts,
            ).order_by(
                Case(
                    When(event_type="outbox.event.dead_letter", then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                "created_at",
            )
            if connection.vendor == "postgresql":
                pending = pending.select_for_update(skip_locked=True)
            event = pending.first()
            if event is None:
                break
            try:
                _deliver_one(event, webhook_url, timeout)
            except Exception as exc:
                new_attempts = event.attempts + 1
                updates = {
                    "attempts": new_attempts,
                    "last_attempt_at": timezone.now(),
                    "last_error": str(exc)[:500],
                }
                if new_attempts >= max_attempts:
                    updates["failed_at"] = timezone.now()
                    OutboxEvent.objects.filter(pk=event.pk).update(**updates)
                    _emit_outbox_dead_letter(event, reason=str(exc)[:240])
                else:
                    OutboxEvent.objects.filter(pk=event.pk).update(**updates)
                logger.warning(
                    "Outbox delivery failed for %s (attempt %s/%s): %s",
                    event.idempotency_key,
                    new_attempts,
                    max_attempts,
                    exc,
                )
                break
            OutboxEvent.objects.filter(pk=event.pk).update(
                delivered_at=timezone.now(),
                attempts=event.attempts + 1,
                last_attempt_at=timezone.now(),
                last_error="",
            )
        delivered += 1
    if delivered:
        logger.info("Delivered %s outbox event(s)", delivered)
    return delivered


def _emit_outbox_dead_letter(event: OutboxEvent, *, reason: str) -> None:
    logger.error(
        "DEAD-LETTER: outbox event %s exhausted delivery retries (%s)",
        event.idempotency_key,
        reason,
    )
    if event.event_type == "outbox.event.dead_letter":
        return
    enqueue_outbox(
        organization_id=event.organization_id,
        event_type="outbox.event.dead_letter",
        idempotency_key=f"outbox:{event.public_id}:dead_letter",
        payload={
            "outbox_id": str(event.public_id),
            "original_event_type": event.event_type,
            "original_idempotency_key": event.idempotency_key,
            "reason": reason,
        },
    )


def purge_failed_outbox_events(*, older_than_days: int | None = None) -> int:
    """Delete outbox rows that exhausted delivery retries past their retention window."""
    if older_than_days is None:
        older_than_days = settings.FILECONVERTER_OUTBOX_TTL_DAYS
    cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
    deleted, _ = OutboxEvent.objects.filter(
        failed_at__isnull=False, failed_at__lt=cutoff
    ).delete()
    if deleted:
        logger.info("Purged %s failed outbox event(s)", deleted)
    return deleted


def purge_delivered_outbox_events(*, older_than_days: int | None = None) -> int:
    """Delete delivered OutboxEvents past their retention window."""
    if older_than_days is None:
        older_than_days = settings.FILECONVERTER_OUTBOX_TTL_DAYS
    cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
    deleted, _ = OutboxEvent.objects.filter(
        delivered_at__isnull=False, delivered_at__lt=cutoff
    ).delete()
    if deleted:
        logger.info("Purged %s delivered outbox event(s)", deleted)
    return deleted
