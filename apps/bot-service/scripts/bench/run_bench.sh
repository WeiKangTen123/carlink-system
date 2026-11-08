#!/usr/bin/env bash
# Runs scripts/benchmark_intake.py for one git ref inside the live carlink_api
# container, on a scratch database and storage, and copies the result JSON
# out to ~/carlink-bench/out/LABEL.json. Runs ON THE SERVER as user weika.
#
#   bash run_bench.sh LABEL GIT_REF [extra benchmark_intake.py options]
#   e.g. bash run_bench.sh baseline-3x feat/bench-harness --repeat 3
#
# The ref's apps/bot-service is taken with git archive -- the live checkout
# in /opt/carlink-system is never switched, so deploys are unaffected -- and
# copied into the container under /tmp/bench/code/LABEL. The live code,
# database and storage are not touched. BENCH_HARNESS_REF=<ref> overlays
# benchmark_intake.py and scripts/bench/ from another ref, to measure a
# branch that doesn't carry this harness yet.
#
# The Gemini key: the live container already has it in its environment, and
# docker exec inherits that. This script never reads, prints or writes it
# (no docker inspect, no printenv, no env file).
#
# Quota: the benchmark's STORAGE_DIR/.ratelimit is a symlink to the live
# /app/storage/.ratelimit, so its calls count in the same 12-per-minute
# window as the live app's (app/ai/rate_limit.py) instead of bypassing it.
#
# PII (photos, key.json, reports, results, logs) stays in ~/carlink-bench
# (0700) and the container's /tmp/bench (0700); none of it goes into git.
# See scripts/bench/README.md.
set -euo pipefail

usage() {
    echo "usage: bash run_bench.sh LABEL GIT_REF [benchmark_intake.py options, e.g. --repeat 3 --only NAME]" >&2
    exit 2
}

die() {
    printf 'run_bench: %s\n' "$*" >&2
    exit 1
}

[ "$#" -ge 2 ] || usage
LABEL=$1
GIT_REF=$2
shift 2

REPO=${BENCH_REPO:-/opt/carlink-system}
CONTAINER=${BENCH_CONTAINER:-carlink_api}
HOME_DIR=${BENCH_HOME:-$HOME/carlink-bench}
HARNESS_REF=${BENCH_HARNESS_REF:-}
# The app refuses uploads below 3 GB free (settings.min_free_disk_gb); a
# run copies every case's photos into the container's /tmp.
MIN_FREE_GB=${BENCH_MIN_FREE_GB:-5}
B=/tmp/bench # inside the container
# The sort cache freezes sorting so reading changes are measured alone;
# BENCH_NO_SORT_CACHE=1 sorts afresh every run (to measure sorting itself).
SORT_CACHE_ARGS=(--sort-cache "$B/sortcache")
if [ "${BENCH_NO_SORT_CACHE:-0}" = 1 ]; then
    SORT_CACHE_ARGS=()
fi

# LABEL names folders under /tmp/bench that this script empties: plain
# characters only, and never one of the shared inputs.
[[ "$LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die "LABEL: letters, digits, '.', '_' or '-' (up to 64), starting with a letter or digit"
case "$LABEL" in
    cases | reports | key.json | code | out | sortcache | storage | run.db) die "LABEL '$LABEL' is a reserved name under $B" ;;
esac
for ref in "$GIT_REF" ${HARNESS_REF:+"$HARNESS_REF"}; do
    [[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$ref" != *..* ]] || die "'$ref' is not a branch, tag or commit name"
done
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || die "BENCH_MIN_FREE_GB must be a whole number"

[ "$(id -u)" -ne 0 ] || die "run as user weika, not root (the files in $HOME_DIR must stay theirs)"
for tool in docker git tar flock df awk; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
done
[ -d "$REPO/.git" ] || die "no git checkout at $REPO (set BENCH_REPO)"

umask 077
mkdir -p "$HOME_DIR/code" "$HOME_DIR/out"
chmod 700 "$HOME_DIR"

# One benchmark at a time: two would share one quota window and slow both.
exec 9>"$HOME_DIR/.run_bench.lock"
flock -n 9 || die "another run_bench.sh is running"

running=$(docker ps --filter "name=^${CONTAINER}\$" --format '{{.Names}}')
[ "$running" = "$CONTAINER" ] || die "container $CONTAINER is not running"

docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null) || docker_root=/
[ -n "$docker_root" ] || docker_root=/
avail_kb=$(df -Pk "$docker_root" 2>/dev/null | awk 'NR == 2 { print $4 }') || avail_kb=
if [ -z "$avail_kb" ]; then
    avail_kb=$(df -Pk / | awk 'NR == 2 { print $4 }')
fi
[ "$avail_kb" -ge $((MIN_FREE_GB * 1024 * 1024)) ] ||
    die "only $((avail_kb / 1024 / 1024)) GB free for Docker; need $MIN_FREE_GB (BENCH_MIN_FREE_GB). Free space first: the app stops taking uploads under 3 GB."

# The inputs are copied in by hand once (they hold real plates and chassis
# numbers, so they come from ~/carlink-bench, never from git).
missing=()
docker exec "$CONTAINER" test -d "$B/cases" || missing+=("cases/")
docker exec "$CONTAINER" test -f "$B/key.json" || missing+=("key.json")
docker exec "$CONTAINER" test -d "$B/reports" || missing+=("reports/")
if [ "${#missing[@]}" -gt 0 ]; then
    {
        echo "run_bench: missing in $CONTAINER:$B: ${missing[*]}"
        echo "Copy them in from $HOME_DIR (a container recreate empties /tmp):"
        echo "  docker exec $CONTAINER sh -c 'mkdir -p $B && chmod 700 $B'"
        echo "  docker cp $HOME_DIR/cases $CONTAINER:$B/cases       # one folder per case, 1600 px photos"
        echo "  docker cp $HOME_DIR/key.json $CONTAINER:$B/key.json"
        echo "  docker cp $HOME_DIR/reports $CONTAINER:$B/reports   # the CL report spreadsheets"
    } >&2
    exit 1
fi

# --- the code: the ref's apps/bot-service, never a checkout of the live repo
fetch_commit() {
    git -C "$REPO" fetch --quiet origin "$1"
    git -C "$REPO" rev-parse --verify 'FETCH_HEAD^{commit}'
}
SHA=$(fetch_commit "$GIT_REF")
REF_NOTE="$GIT_REF@$SHA"
CODE="$HOME_DIR/code/$LABEL"
rm -rf -- "$CODE"
mkdir -p -- "$CODE"
git -C "$REPO" archive --format=tar "$SHA" apps/bot-service | tar -x -C "$CODE"
if [ -n "$HARNESS_REF" ]; then
    HSHA=$(fetch_commit "$HARNESS_REF")
    git -C "$REPO" archive --format=tar "$HSHA" apps/bot-service/scripts/benchmark_intake.py apps/bot-service/scripts/bench |
        tar -x -C "$CODE"
    REF_NOTE="$REF_NOTE (harness $HARNESS_REF@$HSHA)"
fi
[ -f "$CODE/apps/bot-service/scripts/benchmark_intake.py" ] || die "$GIT_REF has no scripts/benchmark_intake.py"
grep -q -- '--sort-cache' "$CODE/apps/bot-service/scripts/benchmark_intake.py" ||
    die "$GIT_REF's benchmark_intake.py predates this harness; set BENCH_HARNESS_REF=feat/bench-harness (or the branch that has it)"

# Fresh scratch for this label; the shared inputs and the sort cache stay.
docker exec "$CONTAINER" sh -c 'umask 077 && mkdir -p "$1/code" "$1/out" "$1/sortcache" && chmod 700 "$1" && rm -rf "$1/code/$2" "$1/$2"' sh "$B" "$LABEL"
docker cp "$CODE" "$CONTAINER:$B/code/$LABEL"
# The live storage's rate-limit window, shared through a symlink. mkdir -p
# only creates it when the live app has never made a call (what its first
# call would do). rm -rf of the scratch folder above removes a link, never
# what it points to.
docker exec "$CONTAINER" sh -c 'mkdir -p /app/storage/.ratelimit "$1/storage" && ln -s /app/storage/.ratelimit "$1/storage/.ratelimit" && [ "$(readlink "$1/storage/.ratelimit")" = /app/storage/.ratelimit ]' sh "$B/$LABEL" ||
    die "couldn't link $B/$LABEL/storage/.ratelimit to the live window"

stop_bench() {
    # docker exec doesn't pass Ctrl-C on to the process in the container.
    echo "run_bench: stopping the benchmark in $CONTAINER" >&2
    docker exec "$CONTAINER" sh -c 'p=$(cat "$1/bench.pid" 2>/dev/null) && kill "$p"' sh "$B/$LABEL" || true
}
trap 'stop_bench; exit 130' INT TERM

LOG="$HOME_DIR/$LABEL.log"
echo "run_bench: $LABEL = $REF_NOTE; log $LOG"
set +e
docker exec \
    -e BENCHMARK_SCRATCH=1 \
    -e "DATABASE_URL=sqlite:///$B/$LABEL/run.db" \
    -e "STORAGE_DIR=$B/$LABEL/storage" \
    -e PYTHONIOENCODING=utf-8 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -w "$B/code/$LABEL/apps/bot-service" \
    "$CONTAINER" sh -c 'echo "$$" > "$0/bench.pid" && exec "$@"' "$B/$LABEL" \
    python "$B/code/$LABEL/apps/bot-service/scripts/benchmark_intake.py" \
    --cases "$B/cases" --key "$B/key.json" --reports "$B/reports" ${SORT_CACHE_ARGS[@]+"${SORT_CACHE_ARGS[@]}"} \
    --out "$B/out/$LABEL.json" --label "$LABEL" --ref "$REF_NOTE" "$@" 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e
trap - INT TERM

# The result file is rewritten after every run, so even a run that stopped
# early has one worth keeping.
if docker exec "$CONTAINER" test -f "$B/out/$LABEL.json"; then
    docker cp "$CONTAINER:$B/out/$LABEL.json" "$HOME_DIR/out/$LABEL.json"
    chmod 600 "$HOME_DIR/out/$LABEL.json"
    echo "run_bench: result $HOME_DIR/out/$LABEL.json (benchmark exit $status)"
    echo "compare:   python3 $CODE/apps/bot-service/scripts/bench/compare.py $HOME_DIR/out/BEFORE.json $HOME_DIR/out/$LABEL.json"
else
    echo "run_bench: no result file was written (benchmark exit $status)" >&2
fi
exit "$status"
