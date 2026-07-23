#!/usr/bin/env python3
# "CLI validator for production env requirements."
"""Validate production deploy environment (DEPLOY1 / DEPLOY2).

Fails when required production controls are missing:
  - SENTRY_DSN when DJANGO_DEBUG=False (unless FILECONVERTER_REQUIRE_SENTRY=False)
  - STAGING_BASE_URL when --require-uptime-url is passed (CI/ops gate)

Usage:
  python scripts/validate_deploy_env.py
  python scripts/validate_deploy_env.py --require-uptime-url
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from apps.ops.deploy_env import validate_deploy_env  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate production deploy environment.")
    parser.add_argument(
        "--require-uptime-url",
        action="store_true",
        help="Require STAGING_BASE_URL (used by uptime-synthetic workflow).",
    )
    args = parser.parse_args(argv)
    errors = validate_deploy_env(require_uptime_url=args.require_uptime_url)
    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        return 1
    print("deploy environment ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
