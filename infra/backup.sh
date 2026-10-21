#!/bin/bash
# ==============================================================================
# CARLINK NIGHTLY BACKUP -- run as root on the VM (root crontab), and by
# deploy.sh before every deploy.
#
#   1. SQLite online backup of the live database (safe while the app writes;
#      a plain `cp carlink.db` misses whatever is still in the -wal file),
#      kept 7 days locally in /root/.carlink-backup/local/.
#   2. Uploaded to gs://$BUCKET/db/ (the bucket deletes db/ copies after 30
#      days).
#   3. Case photos and PDFs (storage/incidents/) mirrored to
#      gs://$BUCKET/incidents/ -- nothing is ever deleted from the mirror, so
#      a case deleted by mistake can still be recovered.
#
# Credentials: an HMAC key of the carlink-backup service account, which can
# only write to this one bucket, in /root/.carlink-backup/hmac.env
# (root-only; RCLONE_CONFIG_GCS_ACCESS_KEY_ID / _SECRET_ACCESS_KEY). Never in
# the repo. HMAC because this project's org policy forbids service-account
# JSON keys, and the VM's own identity only has read-only storage scope.
# Uploads use rclone (pinned Docker image), which speaks GCS's S3 API.
#
# Restore the database:
#   gcloud storage cp gs://$BUCKET/db/carlink-<timestamp>.db /tmp/carlink.db   (any account with bucket read access)
#   docker compose -f infra/docker-compose.prod.yml stop api telegram-bot
#   cp /tmp/carlink.db "$VOL/carlink.db" && rm -f "$VOL/carlink.db-wal" "$VOL/carlink.db-shm"
#   docker compose -f infra/docker-compose.prod.yml start api telegram-bot
# ==============================================================================
set -euo pipefail

BUCKET="${CARLINK_BACKUP_BUCKET:-carlink-backups-steady-hallway-504812-d1}"
HOME_DIR=/root/.carlink-backup
CREDS="$HOME_DIR/hmac.env"
RCLONE_IMAGE="rclone/rclone:1.68.2"
VOLUME="${CARLINK_VOLUME:-infra_bot_data}"

if [ ! -f "$CREDS" ]; then
    echo "backup: no credentials at $CREDS -- backups not configured" >&2
    exit 1
fi

VOL=$(docker volume inspect "$VOLUME" --format '{{.Mountpoint}}')
TS=$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$HOME_DIR/local"
DEST="$HOME_DIR/local/carlink-$TS.db"

python3 - "$VOL/carlink.db" "$DEST" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
n = src.execute("select count(*) from reports").fetchone()[0]
src.close()
print(f"backup: database copied ({n} reports)")
PY

rclone() {
    docker run --rm --env-file "$CREDS" \
        -e RCLONE_CONFIG_GCS_TYPE=s3 -e RCLONE_CONFIG_GCS_PROVIDER=GCS \
        -e RCLONE_CONFIG_GCS_ENDPOINT=https://storage.googleapis.com \
        -e RCLONE_CONFIG_GCS_NO_CHECK_BUCKET=true \
        -v "$HOME_DIR/local:/db:ro" -v "$VOL/incidents:/incidents:ro" \
        "$RCLONE_IMAGE" "$@"
}
rclone copyto "/db/carlink-$TS.db" "gcs:$BUCKET/db/carlink-$TS.db"
# copy, not sync: never deletes from the backup.
rclone copy /incidents "gcs:$BUCKET/incidents" --transfers 4

find "$HOME_DIR/local" -name 'carlink-*.db' -mtime +7 -delete
echo "backup: done $TS -> gs://$BUCKET"
