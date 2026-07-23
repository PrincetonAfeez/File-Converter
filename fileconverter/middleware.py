# "Request ID, CSP, login access, and RLS middleware."
from __future__ import annotations

import contextvars
import logging
import time
import uuid

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.db import connection

# Correlation id for the in-flight request, readable by the logging filter.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


class RequestIDFilter(logging.Filter):
    """Injects the current request id into every log record as %(request_id)s."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class RequestIDMiddleware:
    """Assign/propagate a correlation id per request and echo it on the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        request.request_id = rid
        token = _request_id.set(rid)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = rid
        return response


class ContentSecurityPolicyMiddleware:
    """Set a Content-Security-Policy header (defense-in-depth over template autoescaping)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = getattr(settings, "FILECONVERTER_CSP", "")
        if policy and not response.has_header("Content-Security-Policy"):
            response["Content-Security-Policy"] = policy
        return response


class LoginAccessMiddleware:
    """Log out authenticated users who no longer have any active, non-suspended membership."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not user.is_superuser
            and not user.is_staff
        ):
            from django.contrib.auth import logout
            from django.shortcuts import redirect

            from apps.organizations.services import user_has_login_access

            if not user_has_login_access(user):
                logout(request)
                return redirect(settings.LOGIN_URL)
        return self.get_response(request)


class RowLevelSecurityMiddleware:
    """Opt a web request's DB connection into PostgreSQL row-level tenant isolation.

    Defense-in-depth under the application-layer `accessible_to` scoping: for the duration
    of a request, the connection is restricted to the authenticated user's organizations, so
    even a query that forgot to scope cannot cross tenants. Non-request contexts (Celery
    workers, management commands) never set the scope and run trusted (policy bypassed).
    """

    def __init__(self, get_response):
        from apps.organizations.rls import is_postgres

        if not is_postgres():
            raise MiddlewareNotUsed
        self.get_response = get_response

    def __call__(self, request):
        from apps.organizations.models import Membership, Organization
        from apps.organizations.rls import apply_request_scope, clear_scope

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not user.is_superuser:
            org_ids = list(
                Membership.objects.filter(
                    user=user,
                    status=Membership.Status.ACTIVE,
                    organization__status=Organization.Status.ACTIVE,
                ).values_list("organization_id", flat=True)
            )
            apply_request_scope(org_ids)
        # Superusers and anonymous requests are left trusted (they either need full access or
        # touch no tenant tables behind login_required).
        try:
            return self.get_response(request)
        finally:
            clear_scope()


class SlowQueryMiddleware:
    """Log DB queries slower than FILECONVERTER_SLOW_QUERY_MS. Disabled (removed) when 0."""

    def __init__(self, get_response):
        self.threshold_ms = getattr(settings, "FILECONVERTER_SLOW_QUERY_MS", 0)
        if not self.threshold_ms:
            raise MiddlewareNotUsed
        self.get_response = get_response
        self.logger = logging.getLogger("apps.slowquery")

    def __call__(self, request):
        with connection.execute_wrapper(self._time_query):
            return self.get_response(request)

    def _time_query(self, execute, sql, params, many, context):
        start = time.monotonic()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            if elapsed_ms >= self.threshold_ms:
                self.logger.warning("slow query %.1fms: %s", elapsed_ms, sql[:500])
