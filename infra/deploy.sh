#!/bin/bash
# ==============================================================================
# CARLINK DEPLOY -- one script for both stacks on the shared VM. Run as user
# weika (never root), from any folder:
#
#   bash infra/deploy.sh --env staging --ref <commit|branch|tag>
#   bash infra/deploy.sh --env prod    --ref <commit>     # only what staging tested
#   bash infra/deploy.sh --env prod    --ref <commit> --fast-lane "<why>"
#   bash infra/deploy.sh --env prod|staging --ref <x> --dry-run   # every check, no change
#   bash infra/deploy.sh --env prod|staging --smoke-only          # the checks again, no change
#   bash infra/deploy.sh --env prod|staging --compose -- <docker compose args>
#                                     # by hand, right project and files (= infra/compose.sh)
#
#   production  /opt/carlink-system   compose project "infra"            127.0.0.1:8080
#   staging     /opt/carlink-staging  compose project "carlink-staging"  127.0.0.1:8081
#
# Each stack has its OWN clone (the bot-service code is bind-mounted into
# its containers, so a shared checkout would change what production runs).
# infra/README.md "Staging" has the whole process; in short:
#
#   1. git fetch, then `git checkout --detach <ref>` -- never `git pull`,
#      which takes whatever is newest. The clone must have no local edits.
#   2. production only: the TREE CHECK -- the commit's files must be exactly
#      the files staging last deployed and passed its smoke checks with
#      (/opt/carlink-staging/.deployed), or --fast-lane "<why>" (a small
#      urgent non-AI fix, owner's OK in chat), which is logged.
#   3. production only: a backup first (sudo bash <the release's
#      infra/backup.sh>, local copy at least; it never runs for staging), and
#      the copy must be fresh (.last_backup under 15 minutes old) -- or
#      --no-backup, loudly.
#   4. docker compose (v2) -p <project> -f ... up -d --build; nginx -t in a
#      throwaway container, then a restart of nginx (see below).
#   5. smoke checks: health, 401s when signed out, the worker route 404,
#      every container running with 0 restarts, no Traceback/ERROR in the
#      logs since the deploy, no Telegram token in any log; staging: signups
#      closed. Then <clone>/.deployed (what runs now) and a line in
#      ~/carlink-deploy.log. Exit 2 = deployed, but a smoke check failed.
#
# Secrets are never printed, written to the repo, or passed on a command
# line. Production: GEMINI_API_KEY and TELEGRAM_BOT_TOKEN from the
# environment, else infra/.env, else the running carlink_api container
# (docker exec printenv, straight into this script's environment). Staging:
# its own infra/.env only (0600; infra/staging.env.example) -- the same
# Gemini key as production (owner decision 22 Nov), and NEVER production's
# Telegram token (one poller per token): compared by hash, refused if equal.
#
# Refuses, before changing anything: an unknown option; staging whose
# project or container names would be production's; staging in production's
# folder; staging without the shared AI quota folder (CARLINK_AI_SHARED_DIR,
# /opt/carlink-ai-shared), or while production doesn't mount it (except
# --allow-unshared-prod, for the first staging deploy before production
# runs the guard); production without both secrets; a clone with local edits.
#
# Deployment history: 34.41.243.25 (GCP e2-micro, original) -> 47.84.130.79
# -> 104.154.133.164 (GCP xero-automation; deleted 2026-11-04 with its
# project) -> 34.16.92.98 (GCP instance-20261104-111232, project
# modular-bot-477206-a9, account tenweikan@gmail.com), today's box. It is
# shared with the Solv and Xero apps (host nginx on 80/443, their apps on
# 3000/4000), hence Carlink's nginx on 127.0.0.1:8080 behind the host nginx.
#
# Settings for odd setups and the tests (infra/tests/test_deploy.py):
# CARLINK_PROD_DIR, CARLINK_STAGING_DIR, CARLINK_AI_SHARED_DIR,
# CARLINK_DEPLOY_LOG, CARLINK_SMOKE_WAIT (s, 120), CARLINK_SMOKE_SETTLE (s,
# 30), CARLINK_BACKUP_MAX_AGE (s, 900), CARLINK_REPO_SLUG.
# ==============================================================================
set -euo pipefail

PROD_DIR=${CARLINK_PROD_DIR:-/opt/carlink-system}
STAGING_DIR=${CARLINK_STAGING_DIR:-/opt/carlink-staging}
AI_SHARED_DIR=${CARLINK_AI_SHARED_DIR:-/opt/carlink-ai-shared}
DEPLOY_LOG=${CARLINK_DEPLOY_LOG:-$HOME/carlink-deploy.log}
SMOKE_WAIT=${CARLINK_SMOKE_WAIT:-120}
SMOKE_SETTLE=${CARLINK_SMOKE_SETTLE:-30}
BACKUP_MAX_AGE=${CARLINK_BACKUP_MAX_AGE:-900}
REPO_SLUG=${CARLINK_REPO_SLUG:-WeiKangTen123/carlink-system}
PROD_URL=https://carlink.34-16-92-98.sslip.io
STAGING_URL=https://carlink-staging.34-16-92-98.sslip.io
# A Telegram bot token's shape: never allowed in a log.
TOKEN_RE='[0-9]{8,10}:[A-Za-z0-9_-]{35}'

say() { printf 'deploy: %s\n' "$*"; }
warn() { printf 'deploy: WARNING: %s\n' "$*" >&2; }
die() { printf 'deploy: REFUSED: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

# ------------------------------------------------------------------ options
ENV_NAME=
REF=
FAST_LANE=
FAST_LANE_SET=0
NO_BACKUP=0
DRY_RUN=0
SMOKE_ONLY=0
COMPOSE_ONLY=0
ALLOW_UNSHARED_PROD=0
COMPOSE_ARGS=()
[ "$#" -gt 0 ] || usage
while [ "$#" -gt 0 ]; do
    case "$1" in
        --env) [ "$#" -ge 2 ] || die "--env needs prod or staging"; ENV_NAME=$2; shift 2 ;;
        --env=*) ENV_NAME=${1#--env=}; shift ;;
        --ref) [ "$#" -ge 2 ] || die "--ref needs a commit, branch or tag"; REF=$2; shift 2 ;;
        --ref=*) REF=${1#--ref=}; shift ;;
        --fast-lane) [ "$#" -ge 2 ] || die "--fast-lane needs the reason, in quotes"; FAST_LANE=$2; FAST_LANE_SET=1; shift 2 ;;
        --no-backup) NO_BACKUP=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --smoke-only) SMOKE_ONLY=1; shift ;;
        --allow-unshared-prod) ALLOW_UNSHARED_PROD=1; shift ;;
        --compose) COMPOSE_ONLY=1; shift; [ "${1:-}" = "--" ] && shift; COMPOSE_ARGS=("$@"); break ;;
        -h | --help) usage ;;
        # A typo must never quietly deploy something else, or skip a check.
        *) die "unknown option: $1 (see: bash infra/deploy.sh --help)" ;;
    esac
done

case "$ENV_NAME" in
    prod)
        DIR=$PROD_DIR; PROJECT=infra; NAME=carlink; PORT=8080; URL=$PROD_URL ;;
    staging)
        DIR=$STAGING_DIR; PROJECT=carlink-staging; NAME=carlink_staging; PORT=8081; URL=$STAGING_URL ;;
    "") die "--env prod|staging is required" ;;
    *) die "--env must be prod or staging, not '$ENV_NAME'" ;;
esac
MODES=$((SMOKE_ONLY + COMPOSE_ONLY))
[ "$MODES" -le 1 ] || die "--smoke-only and --compose don't go together"
if [ "$MODES" -eq 0 ]; then
    [ -n "$REF" ] || die "--ref <commit|branch|tag> is required (what to deploy; nothing is taken by default)"
    [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ && "$REF" != *..* ]] || die "'$REF' is not a commit, branch or tag name"
else
    [ -z "$REF" ] || die "--ref is for a deploy, not with --smoke-only / --compose"
    [ "$DRY_RUN" = 0 ] || die "--dry-run is for a deploy"
    [ "$FAST_LANE_SET" = 0 ] && [ "$NO_BACKUP" = 0 ] || die "--fast-lane / --no-backup are for a deploy"
fi
if [ "$FAST_LANE_SET" = 1 ]; then
    [ "$ENV_NAME" = prod ] || die "--fast-lane is for production (staging has no tree check)"
    [ -n "${FAST_LANE// /}" ] || die "--fast-lane needs the reason (it goes in the deploy log)"
fi
[ "$NO_BACKUP" = 0 ] || [ "$ENV_NAME" = prod ] || die "--no-backup is for production (staging is never backed up)"
[ "$ALLOW_UNSHARED_PROD" = 0 ] || [ "$ENV_NAME" = staging ] || die "--allow-unshared-prod is for staging"

[ "$(id -u)" != 0 ] || die "run as user weika, not root (sudo is used only for the backup)"
for tool in git docker curl sha256sum flock; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed"
done
docker compose version >/dev/null 2>&1 ||
    die "Docker Compose v2 ('docker compose') is needed; v1 (docker-compose) ignores the memory caps"

# ------------------------------------------------------------------ helpers

# One value from a KEY=value file (compose's .env), never sourced; empty
# when the file or the key isn't there. The last line for the key wins.
env_value() {
    local file=$1 key=$2 line value=
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        case "$line" in
            "$key="*) value=${line#"$key="} ;;
            "export $key="*) value=${line#"export $key="} ;;
        esac
    done <"$file"
    value=${value#\"}; value=${value%\"}; value=${value#\'}; value=${value%\'}
    printf '%s' "$value"
}

real_dir() { (cd "$1" 2>/dev/null && pwd -P); }

# The compose files of this stack, relative to its clone.
compose_files() {
    FILES=(-f infra/docker-compose.prod.yml)
    if [ "$ENV_NAME" = staging ]; then
        FILES+=(-f infra/docker-compose.staging.yml -f infra/docker-compose.ai-shared.yml)
    elif [ -d "$AI_SHARED_DIR" ]; then
        # Production joins the shared AI quota folder once it exists.
        FILES+=(-f infra/docker-compose.ai-shared.yml)
    fi
}

dc() {
    (cd "$DIR" && CARLINK_AI_SHARED_DIR="$AI_SHARED_DIR" docker compose -p "$PROJECT" "${FILES[@]}" "$@")
}

# sha256 of a value, for comparing tokens without showing them.
hash_of() { printf '%s' "$1" | sha256sum | cut -c1-64; }

# A variable from a running container's environment (never printed here;
# the caller keeps it in a variable). Empty when the container isn't there.
container_env() {
    docker exec "$1" printenv "$2" 2>/dev/null || true
}

record_value() { env_value "$1/.deployed" "$2"; }

# Production's two secrets for compose's interpolation: the environment,
# else infra/.env, else the running carlink_api -- exported into this
# script's environment only, never printed.
prod_secrets() {
    local var value
    for var in GEMINI_API_KEY TELEGRAM_BOT_TOKEN; do
        if [ -z "${!var:-}" ] && [ -z "$(env_value "$ENV_FILE" "$var")" ]; then
            value=$(container_env carlink_api "$var")
            [ -n "$value" ] || die "$var is not set (environment, $ENV_FILE, or the running carlink_api); production needs it"
            printf -v "$var" '%s' "$value"
            export "${var?}"
            value=
        fi
    done
}

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# ------------------------------------------------------------------ the clone
[ -d "$DIR/.git" ] || die "no git clone at $DIR (first setup: infra/README.md \"Staging\")"
ENV_FILE="$DIR/infra/.env"
compose_files

if [ "$ENV_NAME" = staging ]; then
    prod_real=$(real_dir "$PROD_DIR" || true)
    [ "$(real_dir "$DIR")" != "$prod_real" ] || die "staging must have its own clone, never production's ($PROD_DIR)"
fi

if [ "$COMPOSE_ONLY" = 1 ]; then
    if [ "$ENV_NAME" = prod ]; then
        prod_secrets
    else
        [ "$(env_value "$ENV_FILE" CARLINK_NAME)" = carlink_staging ] || die "$ENV_FILE must set CARLINK_NAME=carlink_staging"
    fi
    dc "${COMPOSE_ARGS[@]}"
    exit $?
fi

# One deploy at a time on this box, either stack: two builds at once would
# starve the other apps, and two runs on one clone would mix their commits.
exec 9>"$HOME/.carlink-deploy.lock"
flock -n 9 || die "another deploy.sh is running"

# ------------------------------------------------------------------ smoke checks
SMOKE_FAILS=0
smoke_fail() { printf 'deploy:   FAIL  %s\n' "$*"; SMOKE_FAILS=$((SMOKE_FAILS + 1)); }
smoke_ok() { printf 'deploy:   ok    %s\n' "$*"; }

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:$PORT$1" || true; }

smoke() {
    local since=$1 svc name state body= code n services profiles waited=0
    SMOKE_FAILS=0
    say "smoke checks on 127.0.0.1:$PORT ($ENV_NAME)"
    while :; do
        body=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
        [[ "$body" == *'"ok"'* ]] && break
        [ "$waited" -lt "$SMOKE_WAIT" ] || break
        sleep 5
        waited=$((waited + 5))
    done
    if [[ "$body" == *'"ok"'* ]]; then smoke_ok "/api/health ok"; else smoke_fail "/api/health not ok after ${SMOKE_WAIT}s: ${body:-no answer}"; fi
    if [ "$SMOKE_SETTLE" -gt 0 ]; then
        say "  waiting ${SMOKE_SETTLE}s for any restart to show"
        sleep "$SMOKE_SETTLE"
    fi
    for path in /api/auth/me /api/reports /api/system/info; do
        code=$(http_code "$path")
        if [ "$code" = 401 ]; then smoke_ok "$path 401 signed out"; else smoke_fail "$path answered $code signed out (want 401)"; fi
    done
    code=$(http_code /api/catalogue-worker/claim)
    if [ "$code" = 404 ]; then smoke_ok "worker route 404 from outside"; else smoke_fail "/api/catalogue-worker/claim answered $code (want 404)"; fi
    code=$(http_code /login)
    if [ "$code" = 200 ]; then smoke_ok "/login 200"; else smoke_fail "/login answered $code (want 200)"; fi

    services="api dashboard nginx"
    if [ "$ENV_NAME" = prod ]; then
        services="$services telegram_bot parts_worker"
    else
        profiles=$(env_value "$ENV_FILE" COMPOSE_PROFILES)
        [[ ",$profiles," != *",telegram,"* ]] || services="$services telegram_bot"
        [[ ",$profiles," != *",catalogue,"* ]] || services="$services parts_worker"
    fi
    for svc in $services; do
        name="${NAME}_$svc"
        state=$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$name" 2>/dev/null || echo "missing")
        if [ "$state" = "true 0" ]; then smoke_ok "$name running, 0 restarts"; else smoke_fail "$name: $state (want running, 0 restarts)"; fi
    done
    for svc in api dashboard telegram_bot; do
        name="${NAME}_$svc"
        docker inspect "$name" >/dev/null 2>&1 || continue
        n=$(docker logs --since "$since" "$name" 2>&1 | grep -cE 'Traceback|(^|[^A-Za-z])ERROR([^A-Za-z]|$)' || true)
        if [ "${n:-0}" = 0 ]; then
            smoke_ok "$name log clean since $since"
        else
            smoke_fail "$name: $n Traceback/ERROR line(s) since $since (first lines below)"
            docker logs --since "$since" "$name" 2>&1 | grep -E 'Traceback|(^|[^A-Za-z])ERROR([^A-Za-z]|$)' |
                head -n 5 | sed -E "s/$TOKEN_RE/<token>/g; s/^/deploy:          /" || true
        fi
        n=$(docker logs "$name" 2>&1 | grep -cE "$TOKEN_RE" || true)
        if [ "${n:-0}" = 0 ]; then smoke_ok "$name: no Telegram token in its log"; else smoke_fail "$name: a Telegram token is in its log ($n line(s); not shown)"; fi
    done
    body=$(curl -s --max-time 10 "http://127.0.0.1:$PORT/api/auth/signup" || true)
    if [ "$ENV_NAME" = staging ]; then
        if [[ "$body" == *'"open":false'* || "$body" == *'"open": false'* ]]; then
            smoke_ok "signups closed"
        else
            smoke_fail "signups are not closed on staging (${body:-no answer}): docker exec ${NAME}_api python scripts/seed_staging.py --close-signups-only"
        fi
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/" || true)
        if [ "$code" = 401 ]; then
            smoke_ok "$URL asks for the staging password (401)"
        else
            warn "$URL answered '$code', not 401: is the host nginx site with basic auth in place? (README \"Staging\" step 4)"
        fi
    else
        say "  signups: ${body:-no answer} (production's own switch, not checked)"
    fi
    if [ "$SMOKE_FAILS" = 0 ]; then say "smoke: PASS"; return 0; fi
    say "smoke: FAIL ($SMOKE_FAILS)"
    return 1
}

write_record() {
    local file="$DIR/.deployed" tmp
    tmp="$file.tmp.$$"
    {
        echo "# What runs in this stack now (infra/deploy.sh). The production tree check reads staging's."
        echo "env=$ENV_NAME"
        echo "ref=$REF"
        echo "commit=$SHA"
        echo "tree=$TREE"
        echo "at=$DEPLOYED_AT"
        echo "by=${USER:-$(id -un)}"
        echo "previous=$ROLLBACK"
        echo "smoke=$SMOKE"
        echo "smoke_at=$(now_utc)"
        echo "fast_lane=$FAST_LANE"
        echo "backup=$BACKUP_NOTE"
        echo "compose=${FILES[*]}"
        echo "allow_unshared_prod=$ALLOW_UNSHARED_PROD"
    } >"$tmp"
    mv "$tmp" "$file"
}

if [ "$SMOKE_ONLY" = 1 ]; then
    since=$(record_value "$DIR" at)
    since=${since:-10m}
    if [ "$ENV_NAME" = staging ] && [ -f "$ENV_FILE" ]; then
        PORT=$(env_value "$ENV_FILE" NGINX_PORT); PORT=${PORT:-8081}
    fi
    if smoke "$since"; then SMOKE=pass; else SMOKE=fail; fi
    # The record says what passed: a later run may clear a first one that
    # failed on something passing (the production tree check reads it).
    if [ -f "$DIR/.deployed" ] && [ "$(record_value "$DIR" commit)" = "$(git -C "$DIR" rev-parse HEAD)" ]; then
        sed -i.bak -e "s/^smoke=.*/smoke=$SMOKE/" -e "s/^smoke_at=.*/smoke_at=$(now_utc)/" "$DIR/.deployed"
        rm -f "$DIR/.deployed.bak"
        printf '%s env=%s smoke-only=%s commit=%s by=%s\n' "$(now_utc)" "$ENV_NAME" "$SMOKE" \
            "$(record_value "$DIR" commit)" "${USER:-$(id -un)}" >>"$DEPLOY_LOG"
    fi
    [ "$SMOKE" = pass ]
    exit $?
fi

# ------------------------------------------------------------------ checks before any change
say "$ENV_NAME: $REF -> $DIR (compose project $PROJECT)"

origin=$(git -C "$DIR" remote get-url origin 2>/dev/null || true)
[[ "$origin" == *"$REPO_SLUG"* ]] || die "$DIR's origin is '$origin', not $REPO_SLUG"
dirty=$(git -C "$DIR" status --porcelain --untracked-files=no)
[ -z "$dirty" ] || die "$DIR has local edits to tracked files (a checkout would carry or lose them): git -C $DIR status"

shell_project=${COMPOSE_PROJECT_NAME:-}
file_project=$(env_value "$ENV_FILE" COMPOSE_PROJECT_NAME)
file_name=$(env_value "$ENV_FILE" CARLINK_NAME)
file_port=$(env_value "$ENV_FILE" NGINX_PORT)

if [ "$ENV_NAME" = staging ]; then
    [ -f "$ENV_FILE" ] || die "no $ENV_FILE (cp infra/staging.env.example infra/.env, then fill it: README \"Staging\" step 2)"
    perms=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo 600)
    [[ "$perms" =~ ^[0-7]00$ ]] || die "$ENV_FILE holds a key and is mode $perms: chmod 600 $ENV_FILE"
    for project in "$shell_project" "$file_project"; do
        [ "$project" != infra ] || die "staging's compose project would be 'infra' -- production's (it would take over the live containers)"
    done
    [ -z "$file_project" ] || [ "$file_project" = "$PROJECT" ] || die "$ENV_FILE: COMPOSE_PROJECT_NAME must be $PROJECT, not '$file_project'"
    [ "$file_name" = "$NAME" ] || die "$ENV_FILE: CARLINK_NAME must be $NAME (got '${file_name:-unset}': production's names otherwise)"
    { [ -n "$file_port" ] && [ "$file_port" != 8080 ]; } || die "$ENV_FILE: NGINX_PORT must be set, and not 8080 (production's); 8081"
    [ "$file_port" = "$PORT" ] || warn "$ENV_FILE: NGINX_PORT=$file_port, the host nginx site expects $PORT"
    PORT=$file_port
    [ -n "$(env_value "$ENV_FILE" GEMINI_API_KEY)" ] || die "$ENV_FILE: GEMINI_API_KEY is empty (README \"Staging\" step 2 copies production's without showing it)"
    profiles=$(env_value "$ENV_FILE" COMPOSE_PROFILES)
    if [[ ",$profiles," == *",telegram,"* ]] && [ -z "$(env_value "$ENV_FILE" TELEGRAM_BOT_TOKEN)" ]; then
        die "COMPOSE_PROFILES has telegram but TELEGRAM_BOT_TOKEN is empty (a separate TEST bot's token)"
    fi
    # Never production's Telegram token, nor its worker token: compared by
    # hash, never shown.
    for var in TELEGRAM_BOT_TOKEN CATALOGUE_WORKER_TOKEN; do
        mine=$(env_value "$ENV_FILE" "$var")
        [ -n "$mine" ] || continue
        theirs=$(env_value "$PROD_DIR/infra/.env" "$var")
        [ -n "$theirs" ] || theirs=$(container_env carlink_api "$var")
        [ -n "$theirs" ] || theirs=$(container_env carlink_telegram_bot "$var")
        if [ -z "$theirs" ]; then
            [ "$var" = CATALOGUE_WORKER_TOKEN ] && continue
            die "can't read production's $var to make sure staging's differs (is carlink_api running?)"
        fi
        [ "$(hash_of "$mine")" != "$(hash_of "$theirs")" ] ||
            die "staging's $var is production's (one poller per token: production's bot would miss messages). Use a separate test bot."
        mine=
        theirs=
    done
    # The shared AI quota folder: staging sends no AI request without it,
    # and production must count its own requests there too.
    [ -d "$AI_SHARED_DIR" ] || die "no shared AI quota folder $AI_SHARED_DIR (README \"Staging\" step 3: sudo install -d -m 700 $AI_SHARED_DIR)"
    mounts=$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' carlink_api 2>/dev/null || true)
    if [[ " $mounts " != *" /app/ai-shared "* ]]; then
        if [ "$ALLOW_UNSHARED_PROD" = 1 ]; then
            warn "production's carlink_api doesn't mount $AI_SHARED_DIR yet: staging can't see production's AI use (--allow-unshared-prod). Its own caps still hold."
        else
            die "production's carlink_api doesn't mount the shared AI folder, so staging can't see production's use of the one key. Deploy production with the folder in place first (README \"Staging\" step 3), or --allow-unshared-prod for the first staging deploy only."
        fi
    fi
    flags=$(diff <(grep -E '^(GEMINI_|INTAKE_|TELEGRAM_FULL_DETECTION|CATALOGUE_ENABLED)' "$PROD_DIR/infra/.env" 2>/dev/null | grep -v '^GEMINI_API_KEY=' | sort) \
                 <(grep -E '^(GEMINI_|INTAKE_|TELEGRAM_FULL_DETECTION|CATALOGUE_ENABLED)' "$ENV_FILE" | grep -v '^GEMINI_API_KEY=' | sort) || true)
    if [ -n "$flags" ]; then
        say "flags that differ from production's infra/.env (< production, > staging) -- the sandbox gate's D list:"
        printf '%s\n' "$flags" | grep -E '^[<>]' | sed 's/^/deploy:   /' || true
    else
        say "flags: the same as production's"
    fi
else
    [ -z "$shell_project" ] || [ "$shell_project" = infra ] || die "COMPOSE_PROJECT_NAME=$shell_project is set in this shell; production is 'infra'"
    [ -z "$file_project" ] || [ "$file_project" = infra ] || die "$ENV_FILE: COMPOSE_PROJECT_NAME=$file_project; production is 'infra'"
    [ -z "$file_name" ] || [ "$file_name" = carlink ] || die "$ENV_FILE: CARLINK_NAME=$file_name; production's containers are carlink_*"
    [ -z "$file_port" ] || [ "$file_port" = 8080 ] || warn "$ENV_FILE: NGINX_PORT=$file_port (the host nginx site expects 8080)"
    PORT=${file_port:-8080}
    prod_secrets
    if [ -d "$AI_SHARED_DIR" ]; then
        say "the shared AI quota folder $AI_SHARED_DIR is there: production mounts it (docker-compose.ai-shared.yml)"
    else
        say "no shared AI quota folder ($AI_SHARED_DIR): production keeps its own AI window, as before"
    fi
fi

# ------------------------------------------------------------------ the commit
git -C "$DIR" fetch --quiet --prune --tags origin || die "git fetch failed in $DIR"
SHA=
for candidate in "origin/$REF" "$REF"; do
    SHA=$(git -C "$DIR" rev-parse --verify --quiet "$candidate^{commit}" 2>/dev/null || true)
    [ -z "$SHA" ] || break
done
if [ -z "$SHA" ] && git -C "$DIR" fetch --quiet origin "$REF" 2>/dev/null; then
    SHA=$(git -C "$DIR" rev-parse --verify --quiet 'FETCH_HEAD^{commit}' || true)
fi
[ -n "$SHA" ] || die "no commit '$REF' in $REPO_SLUG (pushed? spelled right?)"
TREE=$(git -C "$DIR" rev-parse "$SHA^{tree}")
ROLLBACK=$(git -C "$DIR" rev-parse HEAD)
say "commit $SHA (tree $TREE); now running $ROLLBACK"

if [ "$ENV_NAME" = prod ]; then
    if [ "$FAST_LANE_SET" = 1 ]; then
        warn "FAST LANE: no tree check against staging. Reason (logged): $FAST_LANE"
    else
        [ -f "$STAGING_DIR/.deployed" ] || die "staging has no deploy record ($STAGING_DIR/.deployed): stage the commit first, or --fast-lane \"<why>\""
        staged_tree=$(record_value "$STAGING_DIR" tree)
        staged_commit=$(record_value "$STAGING_DIR" commit)
        staged_smoke=$(record_value "$STAGING_DIR" smoke)
        [ "$staged_tree" = "$TREE" ] ||
            die "tree check: $SHA's files are not what staging tested (staging runs $staged_commit). Bring the branch up to date with master, restage, rerun the smoke checks -- or --fast-lane \"<why>\"."
        [ "$staged_smoke" = pass ] ||
            die "tree check: staging ran these files but its smoke checks say '${staged_smoke:-nothing}' (bash infra/deploy.sh --env staging --smoke-only)"
        say "tree check: the same files as staging's $staged_commit (smoke pass)"
    fi
fi

if [ "$DRY_RUN" = 1 ]; then
    say "dry run: every check passed; nothing changed. A real run would now:"
    if [ "$ENV_NAME" = prod ]; then
        if [ "$NO_BACKUP" = 1 ]; then say "  - skip the backup (--no-backup)"; else say "  - sudo bash <the release's infra/backup.sh>, and check the copy is fresh"; fi
    fi
    say "  - git -C $DIR checkout --detach $SHA"
    say "  - docker compose -p $PROJECT ${FILES[*]} up -d --build; nginx -t; restart ${NAME}_nginx"
    say "  - smoke checks on 127.0.0.1:$PORT, then $DIR/.deployed and $DEPLOY_LOG"
    exit 0
fi

# ------------------------------------------------------------------ backup (production only)
BACKUP_NOTE=none
if [ "$ENV_NAME" = prod ]; then
    if [ "$NO_BACKUP" = 1 ]; then
        warn "--no-backup: DEPLOYING PRODUCTION WITHOUT A BACKUP. If this goes wrong, the database cannot be put back."
        BACKUP_NOTE=skipped
    else
        # backup.sh decides (exit 0 = a checked local copy, and the upload
        # when a bucket is set); then the copy must be fresh and there. The
        # release's own backup.sh (it doesn't depend on where it sits), so a
        # fix to it applies to the deploy that ships it; the checkout's when
        # the release has none.
        backup_dir=$(mktemp -d)
        backup_script="$backup_dir/backup.sh"
        if ! git -C "$DIR" show "$SHA:infra/backup.sh" >"$backup_script" 2>/dev/null; then
            cp "$DIR/infra/backup.sh" "$backup_script" 2>/dev/null || {
                rm -rf "$backup_dir"
                die "no infra/backup.sh in $SHA or in $DIR: not deploying without a backup (or --no-backup)"
            }
        fi
        say "backing up first (backup.sh of $SHA; local copy at least)"
        sudo bash "$backup_script" || {
            rm -rf "$backup_dir"
            die "backup failed -- not deploying. Fix it (Settings > Company shows the last backup), or --no-backup"
        }
        rm -rf "$backup_dir"
        record=$(docker exec carlink_api cat /app/storage/.last_backup 2>/dev/null || true)
        at=$(printf '%s' "$record" | sed -n 's/.*"at": *"\([^"]*\)".*/\1/p')
        local_path=$(printf '%s' "$record" | sed -n 's/.*"local_path": *"\([^"]*\)".*/\1/p')
        [ -n "$at" ] || die "backup.sh exited 0 but its .last_backup record can't be read: not deploying"
        at_s=$(date -u -d "$at" +%s 2>/dev/null || echo 0)
        age=$(( $(date -u +%s) - at_s ))
        [ "$age" -le "$BACKUP_MAX_AGE" ] || die "the last backup is ${age}s old ($at), not fresh: not deploying"
        { [ -n "$local_path" ] && sudo test -s "$local_path"; } || die "the backup's local copy (${local_path:-none}) isn't there: not deploying"
        BACKUP_NOTE="$at $local_path"
        say "backup fresh: $at"
    fi
fi

# ------------------------------------------------------------------ deploy
DEPLOYED_AT=$(now_utc)
say "checking out $SHA"
git -C "$DIR" checkout --quiet --detach "$SHA"

if ! dc config -q; then
    warn "docker compose config failed on $SHA; putting $ROLLBACK back"
    git -C "$DIR" checkout --quiet --detach "$ROLLBACK"
    die "the compose files of $SHA don't render with $ENV_FILE (nothing was started)"
fi
say "building and starting ($PROJECT)"
if ! dc up -d --build; then
    say "ROLLBACK: bash infra/deploy.sh --env $ENV_NAME --ref $ROLLBACK$([ "$ENV_NAME" = prod ] && echo ' --fast-lane "rollback"')"
    die "docker compose up failed; the stack may be half-updated (see above)"
fi

# nginx looks up the api/dashboard addresses once, when it starts, and
# nginx.conf is a single-file bind mount that a checkout replaces (a new
# inode): a reload would keep the old file. So: nginx -t on the new file in a
# throwaway container on the stack's network, then a restart. If the new
# file is broken, the running nginx keeps its config (reloaded, so it finds
# the recreated containers) and the deploy stops.
if ! docker run --rm --network "${PROJECT}_default" \
        -v "$DIR/infra/nginx.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine nginx -t -q; then
    docker exec "${NAME}_nginx" nginx -s reload || true
    die "infra/nginx.conf fails nginx -t -- nginx keeps its old config. Fix it, then: docker restart ${NAME}_nginx"
fi
docker restart "${NAME}_nginx" >/dev/null

if [ "$ENV_NAME" = staging ]; then
    # Staging never takes signups (owner decision D3). The seed script's
    # switch refuses any stack but staging (CARLINK_STACK).
    docker exec "${NAME}_api" python scripts/seed_staging.py --close-signups-only ||
        warn "couldn't close signups on staging (this commit has no scripts/seed_staging.py?)"
elif ! docker exec "${NAME}_api" python -m app.auth.manage list-users 2>/dev/null | grep -q " admin "; then
    warn "no admin account yet -- create one (it asks for a password):"
    warn "  docker exec -it ${NAME}_api python -m app.auth.manage create-user --email you@example.com --name \"Your Name\" --role admin"
fi

# Build cache: pruned only when the disk is getting full (a cold cache makes
# every build download everything again); dangling images always.
DISK_USE_PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "${DISK_USE_PCT:-0}" -ge 70 ]; then
    say "disk at ${DISK_USE_PCT}% -- pruning the build cache too"
    docker builder prune -af >/dev/null
fi
docker image prune -f >/dev/null

# ------------------------------------------------------------------ smoke + record
if smoke "$DEPLOYED_AT"; then SMOKE=pass; else SMOKE=fail; fi
write_record
printf '%s env=%s commit=%s tree=%s ref=%s by=%s previous=%s smoke=%s backup=%s%s\n' \
    "$DEPLOYED_AT" "$ENV_NAME" "$SHA" "$TREE" "$REF" "${USER:-$(id -un)}" "$ROLLBACK" "$SMOKE" \
    "${BACKUP_NOTE%% *}" "${FAST_LANE:+ fast_lane=\"$FAST_LANE\"}" >>"$DEPLOY_LOG"

say "$ENV_NAME now runs $SHA ($URL). Record: $DIR/.deployed; log: $DEPLOY_LOG"
say "roll back the code: bash infra/deploy.sh --env $ENV_NAME --ref $ROLLBACK$([ "$ENV_NAME" = prod ] && echo ' --fast-lane "rollback"')"
if [ "$SMOKE" != pass ]; then
    say "DEPLOYED, BUT A SMOKE CHECK FAILED (above). Look, then: bash infra/deploy.sh --env $ENV_NAME --smoke-only, or roll back."
    exit 2
fi
