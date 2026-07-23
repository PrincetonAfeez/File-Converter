#!/bin/sh
set -e

# Apply database migrations only where requested (e.g. the web container), so worker
# and beat containers don't race each other to migrate on boot.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Applying database migrations..."
  python manage.py migrate --noinput
fi

exec "$@"
