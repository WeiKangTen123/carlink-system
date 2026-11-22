#!/bin/bash
# ==============================================================================
# CARLINK MIGRATION CHECK -- what a release's database set-up does to a copy
# of production's database. A SANDBOX check: run it BEFORE the release goes to
# staging. Changes nothing in production. Run as root on the server:
#
#   git -C /opt/carlink-staging fetch origin                   # as weika, first
#   sudo bash /opt/carlink-staging/infra/migration_check.sh --ref <commit>
#   sudo bash /opt/carlink-staging/infra/migration_check.sh --ref <commit> <copy.db>
#   sudo bash /opt/carlink-staging/infra/migration_check.sh    # this clone's checkout
#
# --ref: the release's code comes from git (git archive into a temp folder;
# nothing is checked out). Without it: the code this clone has checked out.
# The copy: the newest local backup (made by infra/backup.sh; only READ
# here), or the one named.
#
# Sandbox gate (infra/README.md "Sandbox gate"). The api runs init_db() at
# every start: it creates missing tables, adds missing columns (never drops)
# and runs one-time data steps (reports/db.py _once). Columns roll back
# cleanly; one-time data steps do not -- a release that adds one needs the
# owner to know that a rollback then means a database restore.
#
#   1. Copies the backup into a NEW throwaway Docker volume, in a container
#      with no network.
#   2. Records the copy: integrity, tables, columns, indexes, row counts, the
#      one-time steps already done (app_settings "upgrade:*").
#   3. Runs init_db() on it with the release's code (read-only bind mount) in
#      the staging api's image (else production's -- only its name is read),
#      no network, no ports, no keys -- twice: the second run must change
#      nothing. A release that changes requirements.txt is checked with the
#      older image's packages: after its staging deploy (which builds), run
#      this again without --ref.
#   4. Prints what changed: tables / columns / indexes added, one-time steps
#      run (or failed), row counts before -> after; PASS or FAIL; removes the
#      volume.
#
# Never touches production: no docker exec into it, no production volume, no
# write anywhere but the throwaway volume. The copy holds real data: it stays
# on this box, in that volume, for the length of the run.
#
# FAIL: the copy is damaged, init_db fails or logs a failed step, the second
# run still changes something, or a table lost rows.
#
# Settings: CARLINK_MIGRATION_IMAGE (default: the image carlink_staging_api
# runs, else carlink_api's), CARLINK_BACKUP_DIR (default
# /root/.carlink-backup/local).
# ==============================================================================
set -euo pipefail

LOCAL_DIR=${CARLINK_BACKUP_DIR:-/root/.carlink-backup/local}
REPO=$(cd "$(dirname "$0")/.." && pwd)

die() { echo "migration-check: ERROR: $*" >&2; exit 1; }
say() { echo "migration-check: $*"; }
# Root reading weika's clone: git would refuse it as "dubious ownership".
git_() { git -c safe.directory="$REPO" -C "$REPO" "$@"; }

REF=
if [ "${1:-}" = --ref ]; then
    [ $# -ge 2 ] || die "--ref needs a commit, branch or tag"
    REF=$2
    shift 2
    [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ && "$REF" != *..* ]] || die "'$REF' is not a commit, branch or tag name"
fi
[ $# -le 1 ] || die "usage: sudo bash $0 [--ref <commit>] [<copy.db>]"

[ "$(id -u)" = 0 ] || die "run as root: sudo bash $0 (it reads the root-only backup copies)"

if [ $# -ge 1 ]; then
    FILE=$(readlink -f "$1" || true)
    [ -n "$FILE" ] && [ -f "$FILE" ] || die "no such file: $1"
else
    FILE=$(find "$LOCAL_DIR" -maxdepth 1 -name 'carlink-*.db' -printf '%p\n' 2>/dev/null | sort | tail -n 1 || true)
    [ -n "$FILE" ] || die "no local copy in $LOCAL_DIR (make one: sudo bash /opt/carlink-system/infra/backup.sh)"
fi

IMAGE=${CARLINK_MIGRATION_IMAGE:-}
if [ -z "$IMAGE" ]; then
    IMAGE=$(docker inspect -f '{{.Config.Image}}' carlink_staging_api 2>/dev/null ||
            docker inspect -f '{{.Config.Image}}' carlink_api 2>/dev/null) ||
        die "no carlink_staging_api or carlink_api to take the image from; set CARLINK_MIGRATION_IMAGE"
fi

TMPVOL="carlink_migration_check_$(date -u +%Y%m%d%H%M%S)"
WORK=$(mktemp -d)
cleanup() {
    docker volume rm -f "$TMPVOL" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

if [ -n "$REF" ]; then
    commit=$(git_ rev-parse --verify --quiet "origin/$REF^{commit}" 2>/dev/null ||
             git_ rev-parse --verify --quiet "$REF^{commit}" 2>/dev/null) ||
        die "no commit '$REF' in $REPO (fetch first, as weika: git -C $REPO fetch origin)"
    mkdir -p "$WORK/code"
    git_ archive "$commit" apps/bot-service/app | tar -x -C "$WORK/code"
    CODE="$WORK/code/apps/bot-service/app"
else
    commit=$(git_ rev-parse HEAD 2>/dev/null || echo "?")
    CODE="$REPO/apps/bot-service/app"
fi
[ -d "$CODE" ] || die "no code at $CODE"
say "copy:  $FILE ($(date -u -r "$FILE" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo '?'))"
say "code:  ${REF:-this checkout} = commit ${commit:0:12}, image $IMAGE"

# The database's shape and counts, as JSON (python on stdin, in the image).
cat >"$WORK/snapshot.py" <<'PY'
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
out = {"integrity": db.execute("pragma integrity_check").fetchone()[0], "tables": {}, "columns": {}}
names = [r[0] for r in db.execute(
    "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name")]
for name in names:
    q = name.replace('"', '""')
    out["tables"][name] = db.execute(f'select count(*) from "{q}"').fetchone()[0]
    out["columns"][name] = [r[1] for r in db.execute(f'pragma table_info("{q}")')]
out["indexes"] = sorted(r[0] for r in db.execute(
    "select name from sqlite_master where type = 'index' and name not like 'sqlite_%'"))
out["upgrades"] = sorted(r[0] for r in db.execute(
    "select key from app_settings where key like 'upgrade:%'")) if "app_settings" in names else []
print(json.dumps(out))
PY

# The app's own start-up step, as the api runs it, with its log.
cat >"$WORK/init.py" <<'PY'
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
from app.reports.db import init_db
init_db()
print("init_db finished")
PY

snapshot() {
    docker run --rm -i --network none -v "$TMPVOL:/check" --entrypoint python "$IMAGE" \
        - /check/carlink.db <"$WORK/snapshot.py"
}

run_init() {
    docker run --rm -i --network none -v "$TMPVOL:/app/storage" -v "$CODE:/app/app:ro" \
        -e STORAGE_DIR=/app/storage -e DATABASE_URL=sqlite:////app/storage/carlink.db \
        -e SECRETS_DIR=/tmp/secrets -e RUN_BACKGROUND_CLEANUP=false -e PYTHONDONTWRITEBYTECODE=1 \
        -e GEMINI_API_KEY= -e TELEGRAM_BOT_TOKEN= -e CATALOGUE_WORKER_TOKEN= \
        --entrypoint python "$IMAGE" - <"$WORK/init.py"
}

docker volume create --label carlink.migration-check=1 "$TMPVOL" >/dev/null
docker run --rm --network none --entrypoint cp -v "$FILE:/backup/carlink.db:ro" -v "$TMPVOL:/check" \
    "$IMAGE" /backup/carlink.db /check/carlink.db
say "copied into throwaway volume $TMPVOL (no network)"

snapshot >"$WORK/before.json"
ok1=true
ok2=true
run_init >"$WORK/init1.log" 2>&1 || ok1=false
snapshot >"$WORK/after.json"
run_init >"$WORK/init2.log" 2>&1 || ok2=false
snapshot >"$WORK/again.json"

python3 - "$WORK" "$ok1" "$ok2" <<'PY'
import json, re, sys
from pathlib import Path
work, ok1, ok2 = Path(sys.argv[1]), sys.argv[2] == "true", sys.argv[3] == "true"
before, after, again = (json.loads((work / f"{n}.json").read_text()) for n in ("before", "after", "again"))
logs = [(work / f"init{n}.log").read_text(errors="replace") for n in (1, 2)]
fails, notes = [], []
for label, snap in (("the copy", before), ("after init_db", after)):
    if snap["integrity"] != "ok":
        fails.append(f"integrity_check of {label}: {snap['integrity']}")
for n, (ok, log) in enumerate(zip((ok1, ok2), logs), 1):
    if not ok:
        fails.append(f"init_db run {n} exited with an error (log below)")
    for line in log.splitlines():
        if re.search(r"Start-up step \S+ failed|Traceback|^ERROR ", line):
            fails.append(f"init_db run {n}: {line.strip()[:200]}")

added_tables = sorted(set(after["tables"]) - set(before["tables"]))
gone_tables = sorted(set(before["tables"]) - set(after["tables"]))
added_cols = [f"{t}.{c}" for t in sorted(after["columns"]) if t in before["columns"]
              for c in after["columns"][t] if c not in before["columns"][t]]
added_idx = sorted(set(after["indexes"]) - set(before["indexes"]))
steps = sorted(set(after["upgrades"]) - set(before["upgrades"]))
if gone_tables:
    fails.append(f"tables gone: {', '.join(gone_tables)}")
for t, n in before["tables"].items():
    if t in after["tables"] and after["tables"][t] < n:
        fails.append(f"{t} lost rows: {n} -> {after['tables'][t]}")
if (again["columns"], again["indexes"], again["upgrades"], again["tables"]) != \
        (after["columns"], after["indexes"], after["upgrades"], after["tables"]):
    fails.append("a second init_db changed the database again (not idempotent)")

print("\nWhat the release's init_db did to the copy:")
print(f"  tables added:   {', '.join(added_tables) or 'none'}")
print(f"  columns added:  {', '.join(added_cols) or 'none'}")
print(f"  indexes added:  {', '.join(added_idx) or 'none'}")
print(f"  one-time steps run: {', '.join(s.split(':', 1)[1] for s in steps) or 'none'}")
if steps:
    notes.append("one-time data steps ran: rolling this release back later needs a database restore "
                 "(the pull request must say so, and the owner must know)")
names = sorted(set(before["tables"]) | set(after["tables"]))
width = max([len(n) for n in names] + [5])
print(f"\n  {'table'.ljust(width)}  {'before':>8}  {'after':>8}")
for n in names:
    b, a = before["tables"].get(n), after["tables"].get(n)
    mark = "" if b == a else "  <- changed"
    print(f"  {n.ljust(width)}  {'-' if b is None else b:>8}  {'-' if a is None else a:>8}{mark}")
for n, log in enumerate(logs, 1):
    tail = [l for l in log.splitlines() if l.strip()][-12:]
    print(f"\n  init_db run {n} log (last lines):")
    for line in tail:
        print("    | " + line[:200])
for n in notes:
    print("\nmigration-check: NOTE: " + n)
for f in fails:
    print("migration-check: FAIL: " + f)
print("migration-check: " + ("FAIL" if fails else "PASS") + " (the throwaway volume is removed now)")
sys.exit(1 if fails else 0)
PY
