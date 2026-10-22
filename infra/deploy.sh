#!/bin/bash
# ==============================================================================
# CARLINK SYSTEM — AUTOMATED SERVER DEPLOYMENT SCRIPT
#
# Deployment history:
#  - 34.41.243.25 (GCP e2-micro) -- original box, now cold standby, containers stopped.
#  - 47.84.130.79 -- migrated here next; unreachable via SSH as of 2026-07-30
#    (port 22 accepts the TCP connection but the SSH handshake itself times
#    out/resets -- sshd likely not actually responding), status unknown.
#  - 104.154.133.164 (GCP xero-automation) -- this run's target. Shares the
#    box with an unrelated, already-running xero-invoice-app (nginx on
#    80/443, node app on 3000), so Carlink's nginx uses NGINX_PORT=8080
#    instead -- see docker-compose.prod.yml.
#
# Secrets are never hardcoded here or written into git -- pass them in when
# invoking this script, e.g.:
#
#   TELEGRAM_BOT_TOKEN=xxx GEMINI_API_KEY=yyy NGINX_PORT=8080 ./deploy.sh
#
# ==============================================================================

set -e

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$GEMINI_API_KEY" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN and GEMINI_API_KEY must be set in the environment before running this script."
    echo "   Example: TELEGRAM_BOT_TOKEN=xxx GEMINI_API_KEY=yyy ./deploy.sh"
    exit 1
fi

echo "🚀 Starting Carlink System Deployment..."

# 1. Install Docker & Docker Compose (Debian / Ubuntu)
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker package..."
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose-v2 git curl || sudo apt-get install -y docker-ce docker-compose-plugin
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER" || true
fi

# 2. Clone or Pull GitHub Repository
APP_DIR="/opt/carlink-system"
REPO_URL="https://github.com/WeiKangTen123/carlink-system.git"

if [ ! -d "$APP_DIR" ]; then
    echo "📥 Cloning GitHub Repository $REPO_URL to $APP_DIR..."
    sudo git clone "$REPO_URL" "$APP_DIR"
    sudo chown -R "$USER:$USER" "$APP_DIR"
else
    echo "🔄 Pulling latest updates from GitHub..."
    cd "$APP_DIR"
    git pull origin master
fi

cd "$APP_DIR"

# The site login (nginx basic auth, see infra/nginx.conf). Without this
# file Docker would mount an empty directory in its place and every request
# would fail, so stop here instead. Create it once on the server with:
#   printf 'carlink:%s\n' "$(openssl passwd -apr1)" > infra/carlink.htpasswd
#   chmod 644 infra/carlink.htpasswd
if [ ! -s infra/carlink.htpasswd ]; then
    echo "❌ infra/carlink.htpasswd is missing -- create the site login first (see the comment above this check in deploy.sh)."
    exit 1
fi

# Back up the database and case files before changing anything (see
# infra/backup.sh; the nightly cron runs the same script). A deploy that
# adds tables or columns can't be undone without one.
if sudo test -f /root/.carlink-backup/hmac.env; then
    echo "💾 Backing up before deploy..."
    sudo bash infra/backup.sh || { echo "❌ Backup failed -- not deploying."; exit 1; }
else
    echo "⚠️  Backups not configured on this server (see infra/backup.sh) -- deploying without one."
fi

# 3. Build and Launch Containers using Docker Compose.
# TELEGRAM_BOT_TOKEN / GEMINI_API_KEY are passed straight through from this
# shell's environment via docker-compose's ${VAR} substitution -- nothing
# secret gets written to disk in the repo.
echo "🐳 Building and starting Docker containers..."
export TELEGRAM_BOT_TOKEN
export GEMINI_API_KEY
if command -v docker-compose &> /dev/null; then
    docker-compose -f infra/docker-compose.prod.yml up -d --build
else
    docker compose -f infra/docker-compose.prod.yml up -d --build
fi

# nginx looks up the api/dashboard container addresses once, when it starts.
# `up --build` recreates those two containers (new addresses) but leaves an
# unchanged nginx running, which then proxies to addresses that no longer
# exist: every /api/ and /files/ request returns 502 until nginx is reloaded.
# Hit on the 2026-10-20 deploy (and evidently the one before it, judging by
# nginx's uptime). A reload re-resolves them with no downtime.
echo "🔁 Reloading nginx so it picks up the recreated containers..."
docker exec carlink_nginx nginx -s reload || docker restart carlink_nginx

# Dashboard sign-in (bot-service app/auth): nobody can use the dashboard
# until the first admin account exists. Admins add everyone else from
# Settings -> Users.
if ! docker exec carlink_api python -m app.auth.manage list-users | grep -q " admin "; then
    echo "⚠️  No admin account yet -- create one (it asks for a password):"
    echo "   docker exec -it carlink_api python -m app.auth.manage create-user --email you@example.com --name \"Your Name\" --role admin"
fi

# Only prune the *build cache* when disk is actually getting tight -- it bit
# us hard on the old 10GB e2-micro, but wiping it unconditionally on every
# deploy (as this used to do) means every single build starts ice-cold: full
# npm install, full apt-get, full Playwright Chromium download, every time,
# even for a one-line source change. On a box with real headroom that's pure
# waste. Dangling old image layers are cheap to clean up regardless, so
# those still get pruned every time.
DISK_USE_PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "$DISK_USE_PCT" -ge 70 ]; then
    echo "🧹 Disk at ${DISK_USE_PCT}% -- pruning build cache too..."
    docker builder prune -af
else
    echo "🧹 Disk at ${DISK_USE_PCT}% -- keeping build cache warm for faster next deploy."
fi
docker image prune -f

echo "✅ Carlink System is live!"
echo "🌐 Dashboard: http://<server-ip>:${NGINX_PORT:-80}/"
echo "🤖 Telegram Bot: @carlink_reporter_bot"
echo ""
echo "Note: the API and dashboard containers are no longer published on their"
echo "own host ports (8000/3000) -- everything goes through nginx now. See"
echo "docker-compose.prod.yml for why."
