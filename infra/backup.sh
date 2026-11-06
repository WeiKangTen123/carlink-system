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
#   4. A record of it in the data volume, $VOL/.last_backup (JSON: at,
#      reports, local_path, uploaded, bucket), which Settings > Company shows
#      (GET /system/info last_backup) -- in red when the upload failed or the
#      last backup is old. Written as soon as the local copy exists
#      (uploaded: false) and again after the upload.
#
# Without credentials, or when the upload fails, the local copy is still
# made and kept, nothing local is pruned, the record says uploaded: false,
# and the script exits 1 (deploy.sh then refuses to deploy). Local copies
# only age out (7 days) after an upload that worked -- so while uploads keep
# failing they pile up, one database copy a night, which the red line on
# Settings > Company is there to catch.
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
#
# People's own AI keys (Settings > My AI key) are in the database, sealed
# with ai-keys.secret, which lives in its own volume (infra_ai_secrets,
# mounted at /app/secrets) and is NOT in these backups -- on purpose: a copy
# that leaves the server would otherwise carry the keys and the means to
# open them together. Restoring on the same server needs nothing more. When
# MOVING to another server, copy the secret yourself, as root:
#   old:  cat "$(docker volume inspect infra_ai_secrets --format '{{.Mountpoint}}')/ai-keys.secret"
#   new:  restore the database as above and start the containers (the api
#         makes no new secret for a database that already has keys: System
#         info says "lost"), then put the file at
#         "$(docker volume inspect infra_ai_secrets --format '{{.Mountpoint}}')/ai-keys.secret"
#         (chmod 600). The api and the bot pick it up at their next use.
# Without the old file, start over instead (every saved own key is deleted;
# people add theirs again; company keys aren't touched):
#   docker exec carlink_api python -m app.ai.secretbox reset --confirm
# ==============================================================================
set -euo pipefail

BUCKET="${CARLINK_BACKUP_BUCKET:-carlink-backups-steady-hallway-504812-d1}"
HOME_DIR=/root/.carlink-backup
CREDS="$HOME_DIR/hmac.env"
RCLONE_IMAGE="rclone/rclone:1.68.2"
VOLUME="${CARLINK_VOLUME:-infra_bot_data}"

VOL=$(docker volume inspect "$VOLUME" --format '{{.Mountpoint}}')
TS=$(date -u +%Y%m%d-%H%M%S)
# The same moment in ISO 8601 (UTC), for the record.
AT="${TS:0:4}-${TS:4:2}-${TS:6:2}T${TS:9:2}:${TS:11:2}:${TS:13:2}Z"
mkdir -p "$HOME_DIR/local"
DEST="$HOME_DIR/local/carlink-$TS.db"

# Prints the number of cases, for the record. A failure here stops the
# script before anything is recorded: the last record stays, and ages.
REPORTS=$(python3 - "$VOL/carlink.db" "$DEST" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
n = src.execute("select count(*) from reports").fetchone()[0]
src.close()
print(n)
PY
)
echo "backup: database copied ($REPORTS reports) to $DEST"

# $VOL/.last_backup, read by the api as STORAGE_DIR/.last_backup. Written to
# a temp file and renamed over the old one, so the api never reads half a
# record; world-readable, as the api doesn't run as root.
record() {
    python3 - "$VOL/.last_backup" "$AT" "$REPORTS" "$DEST" "$1" "$BUCKET" <<'PY'
import json, os, sys
path, at, reports, local_path, uploaded, bucket = sys.argv[1:7]
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump({"at": at, "reports": int(reports), "local_path": local_path,
               "uploaded": uploaded == "true", "bucket": bucket or None}, f)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}
# Not uploaded until the upload below says so: if it never finishes (no
# credentials, a crash), that is what the record keeps saying.
record false

if [ ! -f "$CREDS" ]; then
    echo "backup: no credentials at $CREDS -- local copy only ($DEST), NOTHING UPLOADED" >&2
    exit 1
fi

rclone() {
    docker run --rm --env-file "$CREDS" \
        -e RCLONE_CONFIG_GCS_TYPE=s3 -e RCLONE_CONFIG_GCS_PROVIDER=GCS \
        -e RCLONE_CONFIG_GCS_ENDPOINT=https://storage.googleapis.com \
        -e RCLONE_CONFIG_GCS_NO_CHECK_BUCKET=true \
        -v "$HOME_DIR/local:/db:ro" -v "$VOL/incidents:/incidents:ro" \
        "$RCLONE_IMAGE" "$@"
}
# Inside an if, so a failing upload doesn't end the script (set -e) before
# the record says so; uploaded only when both steps worked.
uploaded=false
if rclone copyto "/db/carlink-$TS.db" "gcs:$BUCKET/db/carlink-$TS.db" &&
    # copy, not sync: never deletes from the backup.
    rclone copy /incidents "gcs:$BUCKET/incidents" --transfers 4; then
    uploaded=true
fi
record "$uploaded"

if [ "$uploaded" != true ]; then
    echo "backup: UPLOAD TO gs://$BUCKET FAILED -- local copy kept at $DEST, no local copy deleted" >&2
    exit 1
fi

# Older local copies go only once this one is safely offsite.
find "$HOME_DIR/local" -name 'carlink-*.db' -mtime +7 -delete
echo "backup: done $TS -> gs://$BUCKET"
