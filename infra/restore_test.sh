#!/bin/bash
# ==============================================================================
# CARLINK TEST RESTORE -- proves a backup can be restored, and changes nothing
# in production. Run as root on the server, from the production checkout:
#
#   sudo bash /opt/carlink-system/infra/restore_test.sh            # newest local copy
#   sudo bash /opt/carlink-system/infra/restore_test.sh <copy.db>  # a given copy, e.g.
#                                                                  # one fetched from the bucket
#
#   1. Copies the backup into a NEW throwaway Docker volume (carlink_restore_
#      test_<time>), inside a container with no network.
#   2. Checks the restored copy (PRAGMA integrity_check) and counts the rows of
#      every table in it.
#   3. Counts the rows of the live database, read-only (sqlite mode=ro, via
#      the running api container; nothing is written).
#   4. Starts the app's own database set-up (init_db) on the restored copy, in
#      a throwaway container with no network and no ports, using the code
#      this checkout runs -- what the api would do after a real restore.
#   5. Prints restored vs live counts per table, removes the volume, and exits
#      0 (PASS) or 1 (FAIL).
#
# FAIL: the copy is damaged, init_db fails, or -- when the copy is the one
# .last_backup names -- its case count differs from what backup.sh recorded.
# Differences from the live counts are listed, not failed: people may have
# worked since the backup. Run it straight after `sudo bash infra/backup.sh`
# and the counts should match exactly.
# ==============================================================================
set -euo pipefail

LOCAL_DIR=/root/.carlink-backup/local
PROD_VOLUME=infra_bot_data
API=carlink_api
REPO=$(cd "$(dirname "$0")/.." && pwd)

die() { echo "restore-test: ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root: sudo bash $0"

if [ $# -ge 1 ]; then
    FILE=$(readlink -f "$1")
else
    FILE=$(find "$LOCAL_DIR" -maxdepth 1 -name 'carlink-*.db' -printf '%p\n' 2>/dev/null | sort | tail -n 1)
    [ -n "$FILE" ] || die "no local copy in $LOCAL_DIR (run: sudo bash infra/backup.sh)"
fi
[ -f "$FILE" ] || die "no such file: $FILE"
echo "restore-test: copy under test: $FILE"

# The image the live api runs (already on the server: nothing is pulled).
IMAGE=$(docker inspect -f '{{.Config.Image}}' "$API") || die "container $API not found"
VOL=$(docker volume inspect "$PROD_VOLUME" --format '{{.Mountpoint}}')
TMPVOL="carlink_restore_test_$(date -u +%Y%m%d%H%M%S)"
WORK=$(mktemp -d)
cleanup() {
    docker volume rm -f "$TMPVOL" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

# Per-table row counts + integrity, as JSON on stdout; fed to python on
# stdin. argv: database path inside the container, "ro" to open it read-only.
cat > "$WORK/count.py" <<'PY'
import json, sqlite3, sys
path, ro = sys.argv[1], sys.argv[2] == "ro"
db = sqlite3.connect(f"file:{path}?mode=ro" if ro else path, uri=ro)
out = {"integrity": None if ro else db.execute("pragma integrity_check").fetchone()[0], "tables": {}}
names = [r[0] for r in db.execute(
    "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name")]
for name in names:
    out["tables"][name] = db.execute('select count(*) from "%s"' % name.replace('"', '""')).fetchone()[0]
print(json.dumps(out))
PY

# 1. Into a throwaway volume.
docker volume create --label carlink.restore-test=1 "$TMPVOL" >/dev/null
docker run --rm --network none --entrypoint cp \
    -v "$FILE:/backup/carlink.db:ro" -v "$TMPVOL:/restore" \
    "$IMAGE" /backup/carlink.db /restore/carlink.db
echo "restore-test: restored into throwaway volume $TMPVOL"

# 2. The restored copy.
docker run --rm -i --network none -v "$TMPVOL:/restore" --entrypoint python "$IMAGE" \
    - /restore/carlink.db rw < "$WORK/count.py" > "$WORK/restored.json"

# 3. The live database, read-only.
docker exec -i "$API" python - /app/storage/carlink.db ro < "$WORK/count.py" > "$WORK/live.json"

# 4. The app's own start-up step on the copy (no network, no ports, code from
#    this checkout, as production bind-mounts it).
init_ok=true
docker run --rm --network none -v "$TMPVOL:/app/storage" \
    -v "$REPO/apps/bot-service/app:/app/app:ro" \
    -e STORAGE_DIR=/app/storage -e DATABASE_URL=sqlite:////app/storage/carlink.db \
    -e SECRETS_DIR=/tmp/secrets -e RUN_BACKGROUND_CLEANUP=false -e PYTHONDONTWRITEBYTECODE=1 \
    --entrypoint python "$IMAGE" -c '
from app.reports.db import init_db
init_db()
print("init_db ok")
' > "$WORK/init.log" 2>&1 || init_ok=false
sed 's/^/restore-test:   init_db | /' "$WORK/init.log" | tail -n 20

# 5. Compare and decide.
python3 - "$WORK/restored.json" "$WORK/live.json" "$VOL/.last_backup" "$FILE" "$init_ok" <<'PY'
import json, sys
restored = json.load(open(sys.argv[1]))
live = json.load(open(sys.argv[2]))
record_path, file, init_ok = sys.argv[3], sys.argv[4], sys.argv[5] == "true"
fails, notes = [], []
if restored["integrity"] != "ok":
    fails.append(f"integrity_check of the restored copy: {restored['integrity']}")
if not init_ok:
    fails.append("init_db on the restored copy failed (output above)")
try:
    record = json.load(open(record_path))
except (OSError, ValueError):
    record = None
if record and record.get("local_path") == file:
    got = restored["tables"].get("reports")
    if got != record.get("reports"):
        fails.append(f"reports in the copy: {got}, but backup.sh recorded {record.get('reports')}")
    else:
        notes.append(f"reports = {got}, as backup.sh recorded at {record.get('at')}")
names = sorted(set(restored["tables"]) | set(live["tables"]))
width = max(len(n) for n in names)
print(f"\n{'table'.ljust(width)}  {'restored':>9}  {'live now':>9}")
for n in names:
    r, l = restored["tables"].get(n), live["tables"].get(n)
    mark = "" if r == l else ("  <- not in the copy (added since?)" if r is None else
                              "  <- not live" if l is None else "  <- changed since the backup")
    print(f"{n.ljust(width)}  {'-' if r is None else r:>9}  {'-' if l is None else l:>9}{mark}")
same = sum(1 for n in names if restored["tables"].get(n) == live["tables"].get(n))
print(f"\n{same} of {len(names)} tables have the same row count as live.")
for n in notes:
    print("restore-test: " + n)
for f in fails:
    print("restore-test: FAIL: " + f)
print("restore-test: " + ("FAIL" if fails else "PASS") + " (the throwaway volume is removed now)")
sys.exit(1 if fails else 0)
PY
