# "Throttled login view and access gates."
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView

from apps.files.utils import client_ip_from_request
from fileconverter.ratelimit import counter_value, increment_counter

logger = logging.getLogger(__name__)


class ThrottledLoginView(LoginView):
    """LoginView with cache-based rate limiting to blunt brute-force / credential stuffing.

    Two counters gate a POST: a per-(IP, username) counter and a per-IP aggregate counter
    (the latter catches username-rotating credential stuffing from one source). NOTE:
    enforcement is only accurate with a shared cache backend (Redis); with the default
    per-process LocMemCache the effective limit scales with the number of web processes.
    """

    def _client_ip(self) -> str:
        return client_ip_from_request(self.request)

    def _user_key(self, username: str) -> str:
        return f"login-throttle:{self._client_ip()}:{username or ''}"

    def _ip_key(self) -> str:
        return f"login-throttle-ip:{self._client_ip()}"

    def _submitted_username(self) -> str:
        return (self.request.POST.get("username") or "").strip().lower()

    def _is_blocked(self, username: str) -> bool:
        return (
            counter_value(self._user_key(username)) >= settings.FILECONVERTER_LOGIN_MAX_ATTEMPTS
            or counter_value(self._ip_key()) >= settings.FILECONVERTER_LOGIN_IP_MAX_ATTEMPTS
        )

    def _bump(self, key: str) -> None:
        increment_counter(key, window_seconds=settings.FILECONVERTER_LOGIN_BLOCK_SECONDS)

    def post(self, request, *args, **kwargs):
        username = self._submitted_username()
        if self._is_blocked(username):
            logger.warning("Blocked throttled login attempt from %s", self._client_ip())
            messages.error(
                request, "Too many failed sign-in attempts. Please try again later."
            )
            return self.render_to_response(self.get_context_data(form=self.get_form()), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        self._bump(self._user_key(self._submitted_username()))
        self._bump(self._ip_key())
        return super().form_invalid(form)

    def form_valid(self, form):
        from django.core.cache import cache

        user = form.get_user()
        if user is not None:
            from apps.organizations.services import user_has_login_access

            if not user_has_login_access(user):
                messages.error(
                    self.request,
                    "Your organization has been suspended. Contact support for assistance.",
                )
                return self.form_invalid(form)

        cache.delete(self._user_key(self._submitted_username()))
        return super().form_valid(form)
