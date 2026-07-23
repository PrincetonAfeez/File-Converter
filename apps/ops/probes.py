# "HTTP health and readiness probe helpers."
"""Synthetic health/readiness probe helpers for uptime monitoring."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def fetch_json(url: str, *, timeout: float) -> tuple[int, dict | str]:
    request = urllib.request.Request(url, headers={"User-Agent": "fileconverter-readiness-probe/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        try:
            payload: dict | str = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        return response.status, payload


def ready_ok(
    payload: dict,
    *,
    allow_degraded: frozenset[str] | set[str] | None = None,
) -> tuple[bool, str]:
    """Return whether readiness payload is acceptable.

    ``allow_degraded`` lists check names that may be ``degraded`` without failing the probe
    (e.g. ``{"rls"}`` for CI Postgres superuser roles).
    """
    allowed = set(allow_degraded or ())
    checks = payload.get("checks") or {}
    errors = [name for name, status in checks.items() if status == "error"]
    if errors:
        return False, f"checks in error: {', '.join(errors)}"
    degraded = [name for name, status in checks.items() if status == "degraded"]
    unexpected = [name for name in degraded if name not in allowed]
    if unexpected:
        return False, f"checks degraded: {', '.join(unexpected)}"
    if payload.get("status") not in {"ready", "degraded"}:
        return False, f"unexpected status: {payload.get('status')!r}"
    return True, "ok"


def probe_endpoints(
    base_url: str,
    *,
    health_only: bool = False,
    allow_degraded: frozenset[str] | set[str] | None = None,
    allow_rls_degraded: bool = False,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    allowed = set(allow_degraded or ())
    if allow_rls_degraded:
        allowed.add("rls")

    base = base_url.rstrip("/")
    try:
        status, health = fetch_json(f"{base}/ops/health/", timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"health probe failed: {exc}"
    if status != 200:
        return False, f"health returned HTTP {status}"
    if health_only:
        return True, f"health ok: {health}"

    try:
        status, payload = fetch_json(f"{base}/ops/ready/", timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"readiness probe failed: {exc}"

    if not isinstance(payload, dict):
        return False, f"readiness returned non-JSON body (HTTP {status})"

    ok, reason = ready_ok(payload, allow_degraded=allowed)
    if status not in {200, 503} or not ok:
        return False, f"readiness failed (HTTP {status}): {reason}; checks={payload.get('checks')}"
    return True, f"readiness ok (HTTP {status}): {payload.get('checks')}"
