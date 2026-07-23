# "JSON logging and Sentry initialization."
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_SENTRY_INITIALIZED = False


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log line for ingestion by a log aggregator."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "pid": record.process,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def sentry_initialized() -> bool:
    """True after a successful ``init_sentry()`` call in this process."""
    return _SENTRY_INITIALIZED


def init_sentry() -> bool:
    """Initialize Sentry error tracking when SENTRY_DSN is set (sentry-sdk in requirements.lock).

    Returns True when initialization occurred.
    """
    global _SENTRY_INITIALIZED
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        _SENTRY_INITIALIZED = False
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
    except Exception:  # pragma: no cover - SDK not installed
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; error tracking off")
        _SENTRY_INITIALIZED = False
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
        integrations=[DjangoIntegration(), CeleryIntegration()],
    )
    _SENTRY_INITIALIZED = True
    logger.info("Sentry error tracking initialized")
    return True
