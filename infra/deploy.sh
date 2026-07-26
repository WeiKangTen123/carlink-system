#!/bin/bash
# ==============================================================================
# CARLINK SYSTEM — AUTOMATED SERVER DEPLOYMENT SCRIPT FOR GCP VM (34.41.243.25)
# ==============================================================================

set -e

echo "🚀 Starting Carlink System Deployment on server 34.41.243.25..."

# 1. Install Docker & Docker Compose (Debian / Ubuntu)
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker package..."
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose-v2 git curl || sudo apt-get install -y docker-ce docker-compose-plugin
    sudo systemctl enable --now docker
    sudo usermod -aG docker $USER || true
fi

# 2. Clone or Pull GitHub Repository
APP_DIR="/opt/carlink-system"
REPO_URL="https://github.com/WeiKangTen123/carlink-system.git"

if [ ! -d "$APP_DIR" ]; then
    echo "📥 Cloning GitHub Repository $REPO_URL to $APP_DIR..."
    sudo git clone "$REPO_URL" "$APP_DIR"
    sudo chown -R $USER:$USER "$APP_DIR"
else
    echo "🔄 Pulling latest updates from GitHub..."
    cd "$APP_DIR"
    git pull origin master
fi

cd "$APP_DIR"

# 3. Create .env file for Bot Service
echo "🔑 Writing environment variables..."
cat <<EOT > apps/bot-service/.env
TELEGRAM_BOT_TOKEN=8603705325:AAGJlknQwIXiBFEd8Sn0Jqomv8K4tEWnSi0
GEMINI_MODEL_CHAIN=gemini-2.5-flash,gemini-2.0-flash,gemini-1.5-flash
PORT=8000
STORAGE_DIR=/app/storage
EOT

# 4. Build and Launch Containers using Docker Compose
echo "🐳 Building and starting Docker containers..."
if command -v docker-compose &> /dev/null; then
    docker-compose -f infra/docker-compose.prod.yml up -d --build
else
    docker compose -f infra/docker-compose.prod.yml up -d --build
fi

echo "✅ Carlink System is live!"
echo "🌐 Dashboard Web UI:  http://34.41.243.25:3000"
echo "🌐 Dashboard HTTP:    http://34.41.243.25"
echo "⚙️ Backend API:       http://34.41.243.25:8000"
echo "🤖 Telegram Bot:      @carlink_reporter_bot"
