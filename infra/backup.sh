#!/bin/bash
# ==============================================================================
# CARLINK BACKUP -- PRODUCTION ONLY. Run as root:
#
#   sudo bash /opt/carlink-system/infra/backup.sh
#
# by the nightly cron line (infra/README.md "Backups") and by deploy.sh before
# every production deploy. Never for staging: it refuses any Docker volume that
# is not production's (infra_bot_data of compose project "infra").
#
#   1. SQLite online backup of the live database (safe while the app writes;
#      a plain `cp carlink.db` misses whatever is still in the -wal file) to
#      /root/.carlink-backup/local/carlink-<UTC timestamp>.db, then checked
#      (PRAGMA quick_check). This local copy is the must-have: any failure
#      here exits non-zero, so a deploy stops.
#   2. Only the newest $KEEP_LOCAL local copies are kept (default 14), so the
#      disk can't fill. Deploy-time backups count too.
#   3. OPTIONAL offsite copy, only when a bucket is configured (BACKUP_BUCKET,
#      see below): the database to gs://$BUCKET/db/ and the case photos and
#      PDFs (storage/incidents/) mirrored to gs://$BUCKET/incidents/ (copy,
#      never sync: nothing is ever deleted from the mirror).
#      - BACKUP_BUCKET not set: prints "offsite copy skipped: BACKUP_BUCKET
#        not set" and exits 0 (local copy only -- today's setup).
#      - BACKUP_BUCKET set but no credentials, or the upload fails: exits 1
#        with the reason (deploy.sh then refuses to deploy).
#   4. A record in the data volume, $VOL/.last_backup (JSON: at, reports,
#      local_path, uploaded, bucket), which Settings > Company shows (GET
#      /system/info last_backup). Written once the local copy is checked
#      (uploaded: false) and again after an upload.
#
# Output goes to stdout/stderr only; the cron line decides where it is kept.
#
# Settings (environment first, then /root/.carlink-backup/backup.env, a
# root-only file of KEY=value lines; sudo drops most of the caller's
# environment, so the file is the usual place):
#   BACKUP_BUCKET       bucket name for the offsite copy (no gs://). Unset =
#                       local copy only. There is deliberately no default.
#   BACKUP_KEEP_LOCAL   how many local copies to keep (default 14).
#
# Offsite credentials (only with BACKUP_BUCKET): an HMAC key of a service
# account that can only write to that bucket, in /root/.carlink-backup/
# hmac.env (root-only, 0600): RCLONE_CONFIG_GCS_ACCESS_KEY_ID=... and
# RCLONE_CONFIG_GCS_SECRET_ACCESS_KEY=... Never in the repo. Uploads use
# rclone (pinned Docker image), which speaks Cloud Storage's S3 API.
# Setting it up: infra/README.md "Backups".
#
# Restore (and the test restore that changes nothing): infra/README.md
# "Backups" and infra/restore_test.sh.
#
# People's own AI keys (Settings > My AI key) are in the database, sealed
# with ai-keys.secret, which lives in its own volume (infra_ai_secrets,
# mounted at /app/secrets) and is NOT in these backups -- on purpose: a copy
# that leaves the server would otherwise carry the keys and the means to
# open them together. Restoring on the same server needs nothing more. When
# MOVING to another server, copy the secret yourself, as root:
#   old:  cat "$(docker volume inspect infra_ai_secrets --format '{{.Mountpoint}}')/ai-keys.secret"
#   new:  restore the database as in infra/README.md and start the containers
#         (the api makes no new secret for a database that already has keys:
#         System info says "lost"), then put the file at
#         "$(docker volume inspect infra_ai_secrets --format '{{.Mountpoint}}')/ai-keys.secret"
#         (chmod 600). The api and the bot pick it up at their next use.
# Without the old file, start over instead (every saved own key is deleted;
# people add theirs again; company keys aren't touched):
#   docker exec carlink_api python -m app.ai.secretbox reset --confirm
# ==============================================================================
set -euo pipefail

PROD_VOLUME=infra_bot_data
PROD_PROJECT=infra
HOME_DIR=/root/.carlink-backup
LOCAL_DIR="$HOME_DIR/local"
CONFIG="$HOME_DIR/backup.env"
CREDS="$HOME_DIR/hmac.env"
RCLONE_IMAGE="rclone/rclone:1.68.2"

die() { echo "backup: ERROR: $*" >&2; exit 1; }

TS=$(date -u +%Y%m%d-%H%M%S)
# The same moment in ISO 8601 (UTC), for the record.
AT="${TS:0:4}-${TS:4:2}-${TS:6:2}T${TS:9:2}:${TS:11:2}:${TS:13:2}Z"
echo "backup: start $AT"

[ "$(id -u)" = 0 ] || die "run as root: sudo bash $0"

# A setting from the environment, else from $CONFIG (KEY=value; quotes and
# spaces around the value are dropped). Read, never sourced.
setting() {
    local name="$1" value="${!1:-}"
    if [ -z "$value" ] && [ -f "$CONFIG" ]; then
        value=$(sed -n "s/^[[:space:]]*$name[[:space:]]*=[[:space:]]*//p" "$CONFIG" | tail -n 1 | tr -d "\"'[:space:]")
    fi
    printf '%s' "$value"
}

# --- Production's volume only ---------------------------------------------
# CARLINK_VOLUME exists only so a wrong value is refused loudly: a staging
# stack (compose project carlink-staging) must never write to the backup
# folder or bucket.
VOLUME="${CARLINK_VOLUME:-$PROD_VOLUME}"
if [ "$VOLUME" != "$PROD_VOLUME" ]; then
    die "refusing to back up volume '$VOLUME': backup.sh is for production's $PROD_VOLUME only (staging is never backed up)"
fi
PROJECT=$(docker volume inspect "$VOLUME" --format '{{ index .Labels "com.docker.compose.project" }}') ||
    die "docker volume $VOLUME not found"
if [ -n "$PROJECT" ] && [ "$PROJECT" != "<no value>" ] && [ "$PROJECT" != "$PROD_PROJECT" ]; then
    die "refusing: volume $VOLUME belongs to compose project '$PROJECT', not production's '$PROD_PROJECT'"
fi
VOL=$(docker volume inspect "$VOLUME" --format '{{.Mountpoint}}')
[ -f "$VOL/carlink.db" ] || die "no database at $VOL/carlink.db"

BUCKET=$(setting BACKUP_BUCKET)
BUCKET="${BUCKET#gs://}"
BUCKET="${BUCKET%/}"
if [ -n "$BUCKET" ] && ! [[ "$BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$ ]]; then
    die "BACKUP_BUCKET '$BUCKET' is not a valid bucket name"
fi
KEEP_LOCAL=$(setting BACKUP_KEEP_LOCAL)
KEEP_LOCAL="${KEEP_LOCAL:-14}"
if ! [[ "$KEEP_LOCAL" =~ ^[0-9]+$ ]] || [ "$KEEP_LOCAL" -lt 1 ]; then
    die "BACKUP_KEEP_LOCAL must be a whole number >= 1"
fi

mkdir -p "$LOCAL_DIR"
chmod 700 "$HOME_DIR" "$LOCAL_DIR"
DEST="$LOCAL_DIR/carlink-$TS.db"

# --- 1. Local copy ----------------------------------------------------------
# Prints the number of cases, for the record. A failure here stops the
# script before anything is recorded: the last record stays, and ages.
REPORTS=$(python3 - "$VOL/carlink.db" "$DEST" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
check = dst.execute("pragma quick_check").fetchone()[0]
n = dst.execute("select count(*) from reports").fetchone()[0]
dst.close()
src.close()
if check != "ok":
    sys.exit(f"quick_check of the copy says: {check}")
print(n)
PY
) || { rm -f "$DEST"; die "the local copy failed (nothing recorded; the previous record stays)"; }
chmod 600 "$DEST"
echo "backup: database copied and checked ($REPORTS reports) -> $DEST"

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
# bucket, no credentials, a crash), that is what the record keeps saying.
record false

# --- 2. Local retention: the newest $KEEP_LOCAL copies ----------------------
# Timestamped names sort by age. Runs before the upload, so a failing upload
# can't make copies pile up and fill the disk.
mapfile -t OLD < <(find "$LOCAL_DIR" -maxdepth 1 -name 'carlink-*.db' -printf '%f\n' | sort -r | tail -n +"$((KEEP_LOCAL + 1))")
for name in "${OLD[@]}"; do
    rm -f -- "$LOCAL_DIR/$name"
done
if [ "${#OLD[@]}" -gt 0 ]; then
    echo "backup: removed ${#OLD[@]} old local copies (keeping the newest $KEEP_LOCAL)"
fi

# --- 3. Optional offsite copy -----------------------------------------------
if [ -z "$BUCKET" ]; then
    echo "backup: offsite copy skipped: BACKUP_BUCKET not set (local copy only: $DEST)"
    exit 0
fi
if [ ! -f "$CREDS" ]; then
    echo "backup: ERROR: BACKUP_BUCKET is set ($BUCKET) but there are no credentials at $CREDS -- NOTHING UPLOADED; local copy kept at $DEST" >&2
    exit 1
fi

rclone() {
    docker run --rm --env-file "$CREDS" \
        -e RCLONE_CONFIG_GCS_TYPE=s3 -e RCLONE_CONFIG_GCS_PROVIDER=GCS \
        -e RCLONE_CONFIG_GCS_ENDPOINT=https://storage.googleapis.com \
        -e RCLONE_CONFIG_GCS_NO_CHECK_BUCKET=true \
        -v "$LOCAL_DIR:/db:ro" -v "$VOL/incidents:/incidents:ro" \
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
    echo "backup: ERROR: UPLOAD TO gs://$BUCKET FAILED -- local copy kept at $DEST" >&2
    exit 1
fi
echo "backup: done $TS -> local $DEST and gs://$BUCKET"
