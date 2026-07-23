#!/usr/bin/env python3
# "CLI probe for /ops/health and /ops/ready."
"""Synthetic uptime probe for /ops/ready/ (and optionally /ops/health/).

Used by cron, k8s probes, and .github/workflows/uptime-synthetic.yml to page
on-call when staging/production stops passing readiness checks.

Exit 0 when healthy; exit 1 when a probe fails (suitable for alerting hooks).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/check_readiness.py` from repo root without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from apps.ops.probes import probe_endpoints  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe File Converter health/readiness endpoints.")
    parser.add_argument(
        "base_url",
        help="Base URL without trailing slash (e.g. https://staging.example.com)",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Only probe /ops/health/ (liveness).",
    )
    parser.add_argument(
        "--allow-rls-degraded",
        action="store_true",
        help="Accept readiness when only the rls check is degraded (dev/CI superuser DB).",
    )
    parser.add_argument(
        "--allow-degraded",
        default="",
        help="Comma-separated check names allowed to be degraded (e.g. rls,sentry).",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    args = parser.parse_args(argv)

    allow_degraded = {part.strip() for part in args.allow_degraded.split(",") if part.strip()}
    ok, message = probe_endpoints(
        args.base_url,
        health_only=args.health_only,
        allow_degraded=allow_degraded,
        allow_rls_degraded=args.allow_rls_degraded,
        timeout=args.timeout,
    )
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
