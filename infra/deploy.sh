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

# Clean up build cache and now-superseded image layers left behind by this
# build -- unpruned layers otherwise accumulate forever and can fill a small
# VM's disk over weeks of redeploys (this bit us on the old 10GB e2-micro).
echo "🧹 Cleaning up Docker build cache and old image layers..."
docker builder prune -af
docker image prune -f

echo "✅ Carlink System is live!"
echo "🌐 Dashboard: http://<server-ip>:${NGINX_PORT:-80}/"
echo "🤖 Telegram Bot: @carlink_reporter_bot"
echo ""
echo "Note: the API and dashboard containers are no longer published on their"
echo "own host ports (8000/3000) -- everything goes through nginx now. See"
echo "docker-compose.prod.yml for why."
