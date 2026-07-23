#!/bin/sh
# Restore a backup produced by backup.sh. DESTRUCTIVE: drops and recreates the target DB.
# Usage: restore.sh <db-dump-file> [media-archive.tar.gz]
set -eu

DUMP="${1:?usage: restore.sh <db-dump-file> [media-archive.tar.gz]}"
MEDIA_ARCHIVE="${2:-}"

PGHOST="${POSTGRES_HOST:-localhost}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-fileconverter}"
PGDB="${POSTGRES_DB:-fileconverter}"
export PGPASSWORD="${POSTGRES_PASSWORD:-fileconverter}"

echo "WARNING: this will overwrite database ${PGDB} on ${PGHOST}."
[ "${CONFIRM:-}" = "yes" ] || { echo "Set CONFIRM=yes to proceed."; exit 1; }

echo "Restoring database from ${DUMP}"
pg_restore -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDB" --clean --if-exists --no-owner "$DUMP"

if [ -n "$MEDIA_ARCHIVE" ]; then
  MEDIA_ROOT="${FILECONVERTER_STORAGE_ROOT:-./media}"
  echo "Restoring media into $(dirname "$MEDIA_ROOT")"
  tar -xzf "$MEDIA_ARCHIVE" -C "$(dirname "$MEDIA_ROOT")"
fi

echo "Restore complete. Run 'python manage.py migrate' to reconcile schema if needed."
