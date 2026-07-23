#!/bin/sh
# Database + media backup. Intended to run on a schedule (cron/k8s CronJob) and ship the
# artifact to off-host storage (S3/GCS). Requires: pg_dump, tar, and the app's env vars.
# NOTE: use a pg_dump whose major version MATCHES the server (a newer client can emit
# parameters an older server rejects on restore).
set -eu

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR:-./backups}"
mkdir -p "$DEST"

# --- PostgreSQL ---
PGHOST="${POSTGRES_HOST:-localhost}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-fileconverter}"
PGDB="${POSTGRES_DB:-fileconverter}"
export PGPASSWORD="${POSTGRES_PASSWORD:-fileconverter}"

echo "Dumping database ${PGDB} -> ${DEST}/db-${TS}.dump"
pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDB" -Fc -f "${DEST}/db-${TS}.dump"

# --- Media (input/output blobs) ---
MEDIA_ROOT="${FILECONVERTER_STORAGE_ROOT:-./media}"
if [ -d "$MEDIA_ROOT" ]; then
  echo "Archiving media ${MEDIA_ROOT} -> ${DEST}/media-${TS}.tar.gz"
  tar -czf "${DEST}/media-${TS}.tar.gz" -C "$(dirname "$MEDIA_ROOT")" "$(basename "$MEDIA_ROOT")"
fi

echo "Backup complete: ${DEST}/db-${TS}.dump"
# TODO(ops): ship ${DEST} to off-host object storage and verify the upload.
