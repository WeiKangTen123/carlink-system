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
# infra/README.md has the whole process; in short:
#
#   1. git fetch, then `git checkout --detach <ref>` -- never `git pull`,
#      which takes whatever is newest. The clone must have no local edits.
#   2. production only: the TREE CHECK -- the commit's files must be exactly
#      the files staging last deployed and passed its smoke checks with
#      (/opt/carlink-staging/.deployed), or --fast-lane "<why>" (a small
#      urgent non-AI fix, owner's OK in chat), which is logged.
#   3. production only: a backup first (sudo bash infra/backup.sh of this
#      checkout, else of the release; local copy at least; it never runs for
#      staging), and its record must be newer than the moment it started --
#      or --no-backup, loudly.
#   4. the RENDERED config is checked (docker compose config): project,
#      container names, the one published port, volume names, the AI lane,
#      and -- staging -- that no token is production's. Then the images are
#      BUILT; a failed check or build puts the previous checkout back, so the
#      bind-mounted code always matches the running images. Then up -d;
#      nginx -t in a throwaway container, then a restart of nginx.
#   5. smoke checks: health, 401s when signed out, the worker route 404,
#      every container running and not restarted since the deploy, no
#      Traceback/ERROR in the logs since `up`, no Telegram token in any log;
#      staging: signups closed, the public address asks for the password,
#      production mounts the shared AI folder. Then <clone>/.deployed and a
#      line in ~/carlink-deploy.log. Exit 2 = deployed, a smoke check failed.
#
# Compose runs with a CLEAN environment: every variable the compose files
# read, and COMPOSE_*, are unset first, so nothing exported in the shell
# (a token, a port, a container prefix) can reach a stack. Its values come
# from that stack's infra/.env only -- plus, for production, the two
# secrets: from infra/.env, else the running carlink_api (docker exec
# printenv, into this script's memory, never printed), else the shell (with a
# warning). Staging: its own infra/.env only (0600; staging.env.example) --
# the same Gemini key as production (owner decision 22 Nov), NEVER
# production's Telegram or worker token (compared by hash in the rendered
# config, never shown).
#
# --compose (infra/compose.sh) runs the same clean compose. For production
# only these verbs: ps logs config top images port events exec stats up
# restart start version ls -- no down / rm / kill / stop / volume / build
# (use deploy.sh), no -v / --volumes / --build / -V. After up / restart /
# start it checks the rendered config first and restarts nginx afterwards
# (recreated containers have new addresses; nginx would answer 502).
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
# 30), CARLINK_REPO_SLUG.
# ==============================================================================
set -euo pipefail

PROD_DIR=${CARLINK_PROD_DIR:-/opt/carlink-system}
STAGING_DIR=${CARLINK_STAGING_DIR:-/opt/carlink-staging}
AI_SHARED_DIR=${CARLINK_AI_SHARED_DIR:-/opt/carlink-ai-shared}
DEPLOY_LOG=${CARLINK_DEPLOY_LOG:-$HOME/carlink-deploy.log}
SMOKE_WAIT=${CARLINK_SMOKE_WAIT:-120}
SMOKE_SETTLE=${CARLINK_SMOKE_SETTLE:-30}
REPO_SLUG=${CARLINK_REPO_SLUG:-WeiKangTen123/carlink-system}
PROD_URL=https://carlink.34-16-92-98.sslip.io
STAGING_URL=https://carlink-staging.34-16-92-98.sslip.io
# A Telegram bot token's and a Google API key's shapes: never in a log or a printout.
TOKEN_RE='[0-9]{8,10}:[A-Za-z0-9_-]{35}'
GKEY_RE='AIza[0-9A-Za-z_-]{35}'
# Production's verbs for --compose (review M7).
PROD_VERBS=" ps logs config top images port events exec stats up restart start version ls "

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
for tool in git docker curl sha256sum flock python3; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed"
done
docker compose version >/dev/null 2>&1 ||
    die "Docker Compose v2 ('docker compose') is needed; v1 (docker-compose) ignores the memory caps"

# ------------------------------------------------------------------ helpers

# One value from a KEY=value file, read as compose reads its .env (review
# L5): `export ` allowed, spaces round the value trimmed, quotes removed, an
# unquoted value's " #comment" dropped. Never sourced. Empty when the file
# or the key isn't there; the last line for the key wins.
env_value() {
    local file=$1 key=$2 line value= found=
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        line="${line#"${line%%[![:space:]]*}"}"
        line=${line#export }
        case "$line" in
            "$key="*) found=${line#"$key="} ;;
            *) continue ;;
        esac
        value="${found#"${found%%[![:space:]]*}"}"
        if [[ "$value" == \"* ]]; then
            value=${value#\"}; value=${value%%\"*}
        elif [[ "$value" == \'* ]]; then
            value=${value#\'}; value=${value%%\'*}
        else
            value=${value%%[[:space:]]#*}
            value="${value%"${value##*[![:space:]]}"}"
        fi
    done <"$file"
    printf '%s' "$value"
}

real_dir() { (cd "$1" 2>/dev/null && pwd -P); }

# The compose files of this stack, relative to its clone.
compose_files() {
    FILES=(-f infra/docker-compose.prod.yml)
    if [ "$ENV_NAME" = staging ]; then
        FILES+=(-f infra/docker-compose.staging.yml -f infra/docker-compose.ai-shared.yml)
    elif [ -d "$AI_SHARED_DIR" ] && [ -f "$DIR/infra/docker-compose.ai-shared.yml" ]; then
        # Production joins the shared AI quota folder once it exists -- and
        # only with a commit that has the override (a rollback to an older
        # one keeps its own window, as that code did).
        FILES+=(-f infra/docker-compose.ai-shared.yml)
    fi
}

# Every variable the stack's compose files read (${NAME...}), as they are in
# the clone now, plus compose's own COMPOSE_* settings.
compose_vars() {
    local f files=()
    for f in "${FILES[@]}"; do
        [ "$f" = -f ] || files+=("$DIR/$f")
    done
    { grep -ohE '\$\{[A-Za-z_][A-Za-z0-9_]*' "${files[@]}" 2>/dev/null | cut -c3- || true
      printf '%s\n' COMPOSE_PROFILES COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES COMPOSE_PATH_SEPARATOR \
          COMPOSE_IGNORE_ORPHANS COMPOSE_REMOVE_ORPHANS
    } | sort -u
}

# docker compose for this stack, in a CLEAN environment (review H1, M3):
# whatever the shell exported is unset, so the values come from the stack's
# infra/.env -- and production's two secrets from prod_secrets.
dc() {
    local vars
    vars=$(compose_vars)
    (
        cd "$DIR"
        for v in $vars; do unset "$v"; done
        export CARLINK_AI_SHARED_DIR="$AI_SHARED_DIR"
        if [ "$ENV_NAME" = prod ]; then
            [ -z "${PROD_GEMINI_API_KEY:-}" ] || export GEMINI_API_KEY="$PROD_GEMINI_API_KEY"
            [ -z "${PROD_TELEGRAM_BOT_TOKEN:-}" ] || export TELEGRAM_BOT_TOKEN="$PROD_TELEGRAM_BOT_TOKEN"
        fi
        docker compose -p "$PROJECT" "${FILES[@]}" "$@"
    )
}

# sha256 of a value, for comparing tokens without showing them.
hash_of() { printf '%s' "$1" | sha256sum | cut -c1-64; }

# A variable from a running container's environment (never printed here;
# the caller keeps it in a variable). Empty when the container isn't there.
container_env() {
    docker exec "$1" printenv "$2" 2>/dev/null || true
}

record_value() { env_value "$1/.deployed" "$2"; }

# Production's two secrets, for the rendered config: production's
# infra/.env (compose reads it there itself), else the running carlink_api,
# else this shell (with a warning) -- kept in this script's memory, never
# printed.
prod_secrets() {
    local var value
    for var in GEMINI_API_KEY TELEGRAM_BOT_TOKEN; do
        [ -z "$(env_value "$ENV_FILE" "$var")" ] || continue
        value=$(container_env carlink_api "$var")
        if [ -z "$value" ] && [ -n "${!var:-}" ]; then
            value=${!var}
            warn "$var taken from this shell (not in $ENV_FILE, no running carlink_api)"
        fi
        [ -n "$value" ] || die "$var is not set ($ENV_FILE or the running carlink_api); production needs it"
        printf -v "PROD_$var" '%s' "$value"
        value=
    done
}

# Production's token hashes, for staging's rendered-config check (empty when
# production has none or can't be read).
prod_token_hash() {
    local value
    value=$(env_value "$PROD_DIR/infra/.env" "$1")
    [ -n "$value" ] || value=$(container_env carlink_api "$1")
    [ -n "$value" ] || value=$(container_env carlink_telegram_bot "$1")
    [ -n "$value" ] || return 0
    hash_of "$value"
}

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# The RENDERED config's check (review H1, M3): docker compose config as JSON
# on stdin, only problems printed (the JSON holds secrets: never shown).
read -r -d '' RENDER_CHECK <<'PY' || true
import hashlib, json, os, sys
env_name, project, name, port = sys.argv[1:5]
try:
    data = json.load(sys.stdin)
except ValueError:
    print("deploy:   rendered config: docker compose config failed (its message is above)")
    sys.exit(1)
problems = []
if data.get("name") != project:
    problems.append(f"project is {data.get('name')!r}, not {project!r}")
services = data.get("services") or {}
known = {"api", "dashboard", "nginx", "telegram-bot", "parts-worker"}
for need in ("api", "dashboard", "nginx"):
    if need not in services:
        problems.append(f"no {need} service")
for svc, body in services.items():
    if svc not in known:
        problems.append(f"unexpected service {svc!r}")
        continue
    want = f"{name}_{svc.replace('-', '_')}"
    if body.get("container_name") != want:
        problems.append(f"{svc}: container {body.get('container_name')!r}, not {want!r}")
    ports = body.get("ports") or []
    if svc == "nginx":
        shape = [(str(p.get("host_ip")), str(p.get("published")), str(p.get("target"))) for p in ports]
        if shape != [("127.0.0.1", port, "80")]:
            problems.append(f"nginx publishes {shape}, not 127.0.0.1:{port} -> 80")
    elif ports:
        problems.append(f"{svc} publishes a port")
    for vol in body.get("volumes") or []:
        if vol.get("type") == "volume":
            real = ((data.get("volumes") or {}).get(vol.get("source")) or {}).get("name", "")
            if not real.startswith(f"{project}_"):
                problems.append(f"{svc}: volume {real or vol.get('source')!r} isn't {project}'s")
for key, vol in (data.get("volumes") or {}).items():
    if not str((vol or {}).get("name", "")).startswith(f"{project}_"):
        problems.append(f"volume {key}: {(vol or {}).get('name')!r} isn't {project}'s")
for key, net in (data.get("networks") or {}).items():
    if not str((net or {}).get("name", "")).startswith(f"{project}_"):
        problems.append(f"network {key}: {(net or {}).get('name')!r} isn't {project}'s")
def env(svc):
    e = (services.get(svc) or {}).get("environment") or {}
    return e if isinstance(e, dict) else dict(x.split("=", 1) for x in e if "=" in x)
def h(v):
    return hashlib.sha256((v or "").encode()).hexdigest()
for svc in ("api", "telegram-bot"):
    if svc not in services:
        continue
    e = env(svc)
    if not e.get("GEMINI_API_KEY"):
        problems.append(f"{svc}: no GEMINI_API_KEY")
    lane = e.get("GEMINI_QUOTA_LANE") or "prod"
    if env_name == "staging":
        if lane != "staging":
            problems.append(f"{svc}: AI lane {lane!r}, not staging")
        if not e.get("GEMINI_SHARED_QUOTA_DIR"):
            problems.append(f"{svc}: no shared AI quota folder")
        if e.get("CARLINK_STACK") != "staging":
            problems.append(f"{svc}: CARLINK_STACK isn't staging")
        if "carlink.34-16-92-98.sslip.io" in (e.get("PUBLIC_BASE_URL") or ""):
            problems.append(f"{svc}: PUBLIC_BASE_URL is production's address (set staging's in infra/.env)")
        for var, theirs in (("TELEGRAM_BOT_TOKEN", os.environ.get("PROD_BOT_HASH")),
                            ("CATALOGUE_WORKER_TOKEN", os.environ.get("PROD_WORKER_HASH"))):
            mine = e.get(var) or ""
            if mine and theirs and h(mine) == theirs:
                problems.append(f"{svc}: {var} is production's (one poller per token)")
            if mine and var == "TELEGRAM_BOT_TOKEN" and not theirs:
                problems.append(f"{svc}: a Telegram token is set, but production's can't be read to compare")
    else:
        if lane != "prod":
            problems.append(f"{svc}: AI lane {lane!r}, not prod")
        if e.get("CARLINK_STACK"):
            problems.append(f"{svc}: CARLINK_STACK is set ({e.get('CARLINK_STACK')!r})")
        if not e.get("TELEGRAM_BOT_TOKEN"):
            problems.append(f"{svc}: no TELEGRAM_BOT_TOKEN")
for p in problems:
    print("deploy:   rendered config: " + p)
sys.exit(1 if problems else 0)
PY

# docker compose config, checked; exit 1 (problems printed) when it isn't
# what this stack must be.
render_check() {
    local bot_hash= worker_hash=
    if [ "$ENV_NAME" = staging ]; then
        bot_hash=$(prod_token_hash TELEGRAM_BOT_TOKEN)
        worker_hash=$(prod_token_hash CATALOGUE_WORKER_TOKEN)
    fi
    dc config --format json |
        PROD_BOT_HASH="$bot_hash" PROD_WORKER_HASH="$worker_hash" python3 -c "$RENDER_CHECK" "$ENV_NAME" "$PROJECT" "$NAME" "$PORT"
}

# nginx looks up the api/dashboard addresses once, when it starts, and
# nginx.conf is a single-file bind mount that a checkout replaces (a new
# inode): a reload would keep the old file. So: nginx -t on the new file in a
# throwaway container on the stack's network, then a restart. If the new
# file is broken, the running nginx keeps its config (reloaded, so it finds
# the recreated containers) and this fails.
restart_nginx() {
    if ! docker run --rm --network "${PROJECT}_default" \
            -v "$DIR/infra/nginx.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine nginx -t -q; then
        docker exec "${NAME}_nginx" nginx -s reload || true
        die "infra/nginx.conf fails nginx -t -- nginx keeps its old config. Fix it, then: docker restart ${NAME}_nginx"
    fi
    docker restart "${NAME}_nginx" >/dev/null
    say "restarted ${NAME}_nginx (it finds the recreated containers)"
}

# ------------------------------------------------------------------ the clone
[ -d "$DIR/.git" ] || die "no git clone at $DIR (first setup: infra/README.md)"
ENV_FILE="$DIR/infra/.env"
compose_files

if [ "$ENV_NAME" = staging ]; then
    prod_real=$(real_dir "$PROD_DIR" || true)
    [ "$(real_dir "$DIR")" != "$prod_real" ] || die "staging must have its own clone, never production's ($PROD_DIR)"
    PORT=$(env_value "$ENV_FILE" NGINX_PORT)
    PORT=${PORT:-8081}
else
    # Production is always 127.0.0.1:8080 (the host nginx site's upstream);
    # the rendered-config check holds it to that.
    PORT=8080
fi

if [ "$COMPOSE_ONLY" = 1 ]; then
    verb=
    for a in "${COMPOSE_ARGS[@]}"; do
        case "$a" in -*) ;; *) verb=$a; break ;; esac
    done
    [ -n "$verb" ] || die "--compose needs a docker compose command (ps, logs, up -d, ...)"
    if [ "$ENV_NAME" = prod ]; then
        # Review M7: production's live data is one `down -v` away.
        [[ "$PROD_VERBS" == *" $verb "* ]] ||
            die "'$verb' is not allowed on production here (allowed:$PROD_VERBS). Deploys and rebuilds: deploy.sh; anything else: by hand, deliberately."
        for a in "${COMPOSE_ARGS[@]}"; do
            case "$a" in
                -v | --volumes | --volumes=* | -V | --renew-anon-volumes | --build)
                    die "'$a' is not allowed on production here (volumes are the live data; builds go through deploy.sh)" ;;
            esac
        done
        prod_secrets
    else
        [ "$(env_value "$ENV_FILE" CARLINK_NAME)" = carlink_staging ] || die "$ENV_FILE must set CARLINK_NAME=carlink_staging"
    fi
    case "$verb" in
        up | restart | start)
            render_check || die "the rendered config isn't $ENV_NAME's (above): nothing started"
            dc "${COMPOSE_ARGS[@]}"
            # Review H2: recreated containers have new addresses.
            restart_nginx
            ;;
        *) dc "${COMPOSE_ARGS[@]}" ;;
    esac
    exit 0
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

# "name=count ..." of each running container of the stack: the restart
# counts right after the deploy (review L3: RestartCount counts since the
# container was made, so an unchanged container that once restarted would
# fail every smoke).
restart_counts() {
    local svc out=
    for svc in api dashboard nginx telegram_bot parts_worker; do
        out+="${NAME}_$svc=$(docker inspect -f '{{.RestartCount}}' "${NAME}_$svc" 2>/dev/null || echo x) "
    done
    printf '%s' "$out"
}

smoke() {
    local since=$1 baseline=$2 svc name state running count base body= code n services profiles waited=0 mounts
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
        state=$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$name" 2>/dev/null || echo "missing x")
        running=${state%% *}
        count=${state##* }
        base=$(printf '%s\n' $baseline | sed -n "s/^$name=//p")
        [[ "$base" =~ ^[0-9]+$ ]] || base=0
        if [ "$running" = true ] && [[ "$count" =~ ^[0-9]+$ ]] && [ "$count" -le "$base" ]; then
            smoke_ok "$name running, not restarted since the deploy"
        else
            smoke_fail "$name: running=$running, restarts $count (was $base after the deploy)"
        fi
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
                head -n 5 | sed -E "s/$TOKEN_RE/<token>/g; s/$GKEY_RE/<key>/g; s/^/deploy:          /" || true
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
        # Review L9: until https asks for the password, don't use staging.
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/" || true)
        if [ "$code" = 401 ]; then
            smoke_ok "$URL asks for the staging password (401)"
        else
            smoke_fail "$URL answered '$code', not 401: the host nginx site with basic auth and https isn't in place -- don't use staging until it is (README first setup, step 6)"
        fi
        # Review L14: production must still count its AI use in the shared folder.
        mounts=$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' carlink_api 2>/dev/null || true)
        if [[ " $mounts " == *" /app/ai-shared "* ]]; then
            smoke_ok "production mounts the shared AI folder"
        elif [ "$ALLOW_UNSHARED_PROD" = 1 ] || [ "$(record_value "$DIR" allow_unshared_prod)" = 1 ]; then
            warn "production doesn't mount the shared AI folder yet (--allow-unshared-prod)"
        else
            smoke_fail "production's carlink_api doesn't mount the shared AI folder (a by-hand compose without it?): bash infra/compose.sh prod up -d"
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
        echo "up_at=$UP_AT"
        echo "by=${USER:-$(id -un)}"
        echo "previous=$ROLLBACK"
        echo "smoke=$SMOKE"
        echo "smoke_at=$(now_utc)"
        echo "fast_lane=$FAST_LANE"
        echo "backup=$BACKUP_NOTE"
        echo "compose=${FILES[*]}"
        echo "allow_unshared_prod=$ALLOW_UNSHARED_PROD"
        echo "restarts=$RESTARTS"
    } >"$tmp"
    mv "$tmp" "$file"
}

if [ "$SMOKE_ONLY" = 1 ]; then
    since=$(record_value "$DIR" up_at)
    since=${since:-$(record_value "$DIR" at)}
    since=${since:-10m}
    if smoke "$since" "$(record_value "$DIR" restarts)"; then SMOKE=pass; else SMOKE=fail; fi
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

file_project=$(env_value "$ENV_FILE" COMPOSE_PROJECT_NAME)
file_name=$(env_value "$ENV_FILE" CARLINK_NAME)
file_port=$(env_value "$ENV_FILE" NGINX_PORT)
[ -z "${COMPOSE_PROJECT_NAME:-}${CARLINK_NAME:-}${NGINX_PORT:-}${TELEGRAM_BOT_TOKEN:-}" ] ||
    say "note: compose variables exported in this shell are ignored (each stack takes them from its own infra/.env)"

if [ "$ENV_NAME" = staging ]; then
    [ -f "$ENV_FILE" ] || die "no $ENV_FILE (cp infra/staging.env.example infra/.env, then fill it: README first setup, step 4)"
    perms=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo 600)
    [[ "$perms" =~ ^[0-7]00$ ]] || die "$ENV_FILE holds a key and is mode $perms: chmod 600 $ENV_FILE"
    [ "$file_project" != infra ] || die "staging's compose project would be 'infra' -- production's (it would take over the live containers)"
    [ -z "$file_project" ] || [ "$file_project" = "$PROJECT" ] || die "$ENV_FILE: COMPOSE_PROJECT_NAME must be $PROJECT, not '$file_project'"
    [ "$file_name" = "$NAME" ] || die "$ENV_FILE: CARLINK_NAME must be $NAME (got '${file_name:-unset}': production's names otherwise)"
    { [ -n "$file_port" ] && [ "$file_port" != 8080 ]; } || die "$ENV_FILE: NGINX_PORT must be set, and not 8080 (production's); 8081"
    [ "$file_port" = 8081 ] || warn "$ENV_FILE: NGINX_PORT=$file_port, the host nginx site expects 8081"
    [ -n "$(env_value "$ENV_FILE" GEMINI_API_KEY)" ] || die "$ENV_FILE: GEMINI_API_KEY is empty (README first setup copies production's without showing it)"
    profiles=$(env_value "$ENV_FILE" COMPOSE_PROFILES)
    if [[ ",$profiles," == *",telegram,"* ]] && [ -z "$(env_value "$ENV_FILE" TELEGRAM_BOT_TOKEN)" ]; then
        die "COMPOSE_PROFILES has telegram but TELEGRAM_BOT_TOKEN is empty (a separate TEST bot's token)"
    fi
    # The shared AI quota folder: staging sends no AI request without it,
    # and production must count its own requests there too.
    [ -d "$AI_SHARED_DIR" ] || die "no shared AI quota folder $AI_SHARED_DIR (README first setup, step 2: sudo install -d -m 700 $AI_SHARED_DIR)"
    mounts=$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' carlink_api 2>/dev/null || true)
    if [[ " $mounts " != *" /app/ai-shared "* ]]; then
        if [ "$ALLOW_UNSHARED_PROD" = 1 ]; then
            warn "production's carlink_api doesn't mount $AI_SHARED_DIR yet: staging can't see production's AI use (--allow-unshared-prod). Its own caps still hold."
        else
            die "production's carlink_api doesn't mount the shared AI folder, so staging can't see production's use of the one key. Deploy production with the folder in place first, or --allow-unshared-prod for the first staging deploy only."
        fi
    fi
    # The flags list for gate D. No secret is ever listed (review L4): names
    # with KEY / TOKEN / SECRET / PASS are left out, and anything shaped like
    # a Google key or a bot token is masked.
    flag_lines() {
        grep -E '^(GEMINI_|INTAKE_|TELEGRAM_FULL_DETECTION|CATALOGUE_ENABLED)' "$1" 2>/dev/null |
            grep -vE '^[A-Z0-9_]*(KEY|TOKEN|SECRET|PASS)[A-Z0-9_]*=' |
            sed -E "s/$GKEY_RE/<key>/g; s/$TOKEN_RE/<token>/g" | sort || true
    }
    flags=$(diff <(flag_lines "$PROD_DIR/infra/.env") <(flag_lines "$ENV_FILE") || true)
    if [ -n "$flags" ]; then
        say "flags that differ from production's infra/.env (< production, > staging) -- the gate's D list:"
        printf '%s\n' "$flags" | grep -E '^[<>]' | sed 's/^/deploy:   /' || true
    else
        say "flags: the same as production's"
    fi
else
    [ -z "$file_project" ] || [ "$file_project" = infra ] || die "$ENV_FILE: COMPOSE_PROJECT_NAME=$file_project; production is 'infra'"
    [ -z "$file_name" ] || [ "$file_name" = carlink ] || die "$ENV_FILE: CARLINK_NAME=$file_name; production's containers are carlink_*"
    [ -z "$file_port" ] || [ "$file_port" = 8080 ] || die "$ENV_FILE: NGINX_PORT=$file_port; production is 127.0.0.1:8080 (the host nginx site's upstream)"
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

# The rendered config of the CURRENT checkout's compose files with this
# .env and a clean environment, before anything changes (the release's own
# files are checked again after the checkout). Skipped when the checkout is
# older than these files (the first deploy of this tooling).
current_ok=1
for f in "${FILES[@]}"; do
    [ "$f" = -f ] || [ -f "$DIR/$f" ] || current_ok=0
done
if [ "$current_ok" = 1 ]; then
    render_check || die "the rendered config (this checkout's compose files, $ENV_FILE) isn't $ENV_NAME's (above): nothing changed"
    say "rendered config ok (this checkout's files, $ENV_FILE, a clean environment)"
else
    say "this checkout lacks some of the compose files: the rendered config is checked after the checkout"
fi

if [ "$DRY_RUN" = 1 ]; then
    say "dry run: every check passed; nothing changed. A real run would now:"
    if [ "$ENV_NAME" = prod ]; then
        if [ "$NO_BACKUP" = 1 ]; then say "  - skip the backup (--no-backup)"; else say "  - sudo bash infra/backup.sh (this checkout's, else the release's), and check its record is new"; fi
    fi
    say "  - git -C $DIR checkout --detach $SHA; check the rendered config; build"
    say "    (a failed check or build puts $ROLLBACK back)"
    say "  - docker compose -p $PROJECT ${FILES[*]} up -d; nginx -t; restart ${NAME}_nginx"
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
        # when a bucket is set); then its record must be the one it just
        # wrote (review L8: read from the volume, not through carlink_api,
        # which may be down) and the copy there. The checkout's backup.sh
        # first (reviewed, already running here); if it fails, the release's
        # own (it doesn't depend on where it sits) -- so a release that FIXES
        # the backup (the first deploy of D1's) deploys, and a rollback to an
        # older commit backs up with today's script.
        backup_dir=$(mktemp -d)
        backup_ok=0
        started_s=$(date -u +%s)
        say "backing up first (this checkout's backup.sh; local copy at least)"
        if [ -f "$DIR/infra/backup.sh" ] && sudo bash "$DIR/infra/backup.sh"; then
            backup_ok=1
        elif git -C "$DIR" show "$SHA:infra/backup.sh" >"$backup_dir/backup.sh" 2>/dev/null; then
            warn "this checkout's backup.sh failed or is missing: trying the release's (backup.sh of $SHA)"
            started_s=$(date -u +%s)
            sudo bash "$backup_dir/backup.sh" && backup_ok=1
        fi
        rm -rf "$backup_dir"
        [ "$backup_ok" = 1 ] ||
            die "backup failed -- not deploying. Fix it (Settings > Company shows the last backup), or --no-backup"
        vol_dir=$(docker volume inspect infra_bot_data --format '{{.Mountpoint}}' 2>/dev/null || true)
        record=
        [ -z "$vol_dir" ] || record=$(sudo cat "$vol_dir/.last_backup" 2>/dev/null || true)
        at=$(printf '%s' "$record" | sed -n 's/.*"at": *"\([^"]*\)".*/\1/p')
        local_path=$(printf '%s' "$record" | sed -n 's/.*"local_path": *"\([^"]*\)".*/\1/p')
        [ -n "$at" ] || die "backup.sh exited 0 but its .last_backup record can't be read from infra_bot_data: not deploying"
        at_s=$(date -u -d "$at" +%s 2>/dev/null || echo 0)
        [ "$at_s" -ge $((started_s - 1)) ] || die "the backup record ($at) is older than this backup run: backup.sh didn't write a new copy. Not deploying"
        { [ -n "$local_path" ] && sudo test -s "$local_path"; } || die "the backup's local copy (${local_path:-none}) isn't there: not deploying"
        BACKUP_NOTE="$at $local_path"
        say "backup made: $at"
    fi
fi

# ------------------------------------------------------------------ deploy
DEPLOYED_AT=$(now_utc)
put_back() {
    git -C "$DIR" checkout --quiet --detach "$ROLLBACK"
    warn "$DIR is back on $ROLLBACK (the running containers were not touched)"
}
say "checking out $SHA"
git -C "$DIR" checkout --quiet --detach "$SHA"
# The release's own compose files decide (production: the shared AI override
# only when this commit has it).
compose_files

if ! render_check; then
    put_back
    die "the rendered config of $SHA isn't $ENV_NAME's (above): nothing was started"
fi
# Review M4: the images first. If the build fails, the old checkout goes
# back, so the bind-mounted code always matches the images that run.
say "building ($PROJECT)"
if ! dc build; then
    put_back
    die "docker compose build failed on $SHA (see above); nothing was started"
fi
say "starting ($PROJECT)"
if ! dc up -d --no-build; then
    say "ROLLBACK: bash infra/deploy.sh --env $ENV_NAME --ref $ROLLBACK$([ "$ENV_NAME" = prod ] && echo ' --fast-lane "rollback"')"
    die "docker compose up failed; the stack may be half-updated (see above)"
fi
restart_nginx
UP_AT=$(now_utc)
RESTARTS=$(restart_counts)

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
# every build download everything again); dangling images always. A prune
# that fails (another one running) never stops the smoke and the record
# (review L2).
DISK_USE_PCT=$(df --output=pcent / 2>/dev/null | tail -1 | tr -dc '0-9' || true)
if [ "${DISK_USE_PCT:-0}" -ge 70 ]; then
    say "disk at ${DISK_USE_PCT}% -- pruning the build cache too"
    docker builder prune -af >/dev/null || warn "docker builder prune failed (ignored)"
fi
docker image prune -f >/dev/null || warn "docker image prune failed (ignored)"

# ------------------------------------------------------------------ smoke + record
if smoke "$UP_AT" "$RESTARTS"; then SMOKE=pass; else SMOKE=fail; fi
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
