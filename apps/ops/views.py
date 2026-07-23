# "Health, readiness, and metrics HTTP views."
from __future__ import annotations

import hmac
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

from apps.files.utils import mime_scanner_available
from fileconverter.celery import app

logger = logging.getLogger(__name__)


def _metrics_authorized(request) -> bool:
    """Metrics carry aggregate operational data (job counts) — never anonymous.

    Allowed via a scraper bearer token (FILECONVERTER_METRICS_TOKEN) or a staff session.
    """
    token = settings.FILECONVERTER_METRICS_TOKEN
    if token:
        header = request.META.get("HTTP_AUTHORIZATION", "")
        prefix = "Bearer "
        if header.startswith(prefix) and hmac.compare_digest(header[len(prefix):], token):
            return True
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


def metrics(request):
    """Operational gauges for scraping: queue depth, job status counts, dead-letters."""
    if not _metrics_authorized(request):
        return JsonResponse({"detail": "forbidden"}, status=403)
    from .metrics import collect_metrics

    return JsonResponse(collect_metrics())


def health(request):
    return JsonResponse({"status": "ok"})


def _check_database() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return "ok"


def _check_cache() -> str:
    cache.set("ops:readiness", "1", 5)
    return "ok" if cache.get("ops:readiness") == "1" else "error"


def _check_broker() -> str:
    conn = app.connection()
    try:
        conn.ensure_connection(max_retries=1, timeout=2)
        return "ok"
    finally:
        conn.release()


def _check_mime_scanner() -> str:
    if not settings.FILECONVERTER_ENFORCE_MIME_MATCH:
        return "ok"
    if mime_scanner_available():
        return "ok"
    # Enforcement is on but libmagic is missing: hard failure only when required to be
    # present, otherwise a visible "degraded" that does not pull the node from rotation.
    return "error" if settings.FILECONVERTER_REQUIRE_MIME_SCANNER else "degraded"


def _check_rls() -> str:
    """Warn if row-level security is defeated by a superuser DB role.

    PostgreSQL superusers (and roles with BYPASSRLS) ignore RLS policies, silently disabling
    the tenant-isolation defense-in-depth. Degraded (not error): app-layer scoping still
    protects, but the DB layer is not enforcing.
    """
    if connection.vendor != "postgresql":
        return "ok"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        row = cursor.fetchone()
    return "degraded" if row and row[0] else "ok"


def _check_sentry() -> str:
    """Surface missing error tracking in production (DEPLOY2).

    Dev (DEBUG) and explicit FILECONVERTER_REQUIRE_SENTRY=False skip this gate.
    """
    if settings.DEBUG or not settings.FILECONVERTER_REQUIRE_SENTRY:
        return "ok"
    from fileconverter.observability import sentry_initialized

    return "ok" if sentry_initialized() else "degraded"


def readiness(request):
    checks = {
        "database": _check_database,
        "cache": _check_cache,
        "broker": _check_broker,
        "mime_scanner": _check_mime_scanner,
        "rls": _check_rls,
        "sentry": _check_sentry,
    }
    results: dict[str, str] = {}
    ok = True
    for name, check in checks.items():
        try:
            results[name] = check()
        except Exception:
            logger.exception("Readiness check failed: %s", name)
            results[name] = "error"
        if results[name] in {"error", "degraded"}:
            ok = False
    status_code = 200 if ok else 503
    overall = "ready" if ok else "degraded"
    return JsonResponse({"status": overall, "checks": results}, status=status_code)
