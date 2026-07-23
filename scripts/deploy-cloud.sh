#!/usr/bin/env bash
# =============================================================================
# NarraNexus Cloud Deployment Script
#
# Usage:
#   1. Clone the repo on your EC2 instance
#   2. Copy .env.cloud.example → .env and fill in MySQL credentials
#   3. Run: bash scripts/deploy-cloud.sh
#
# Prerequisites: Ubuntu 22.04, internet access
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVICE_USER="${USER}"

C="\033[36m"; G="\033[32m"; Y="\033[33m"; R="\033[0m"; RED="\033[31m"

echo -e "${C}"
echo '  _   _                    _   _                    '
echo ' | \ | | __ _ _ __ _ __ __|  \| | _____  ___   _ ___'
echo ' |  \| |/ _` |  __|  __/ _` | |` |/ _ \ \/ / | | / __|'
echo ' | |\ | (_| | |  | | | (_| | |\ |  __/>  <| |_| \__ \'
echo ' |_| \_|\__,_|_|  |_|  \__,_|_| \_|\___/_/\_\\__,_|___/'
echo -e "${R}"
echo -e "  ${G}Cloud Deployment${R}"
echo ""

# --- Check .env exists ---
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${RED}Error: .env file not found.${R}"
    echo "Copy the example and fill in your values:"
    echo "  cp .env.cloud.example .env"
    exit 1
fi

# Source .env
set -a
source "$PROJECT_ROOT/.env"
set +a

# --- Install system dependencies ---
echo -e "${Y}[1/6] Installing system dependencies...${R}"
sudo apt-get update -qq
sudo apt-get install -y -qq nginx curl git build-essential

# Install Node.js 20 if not present
if ! command -v node &>/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
    echo -e "${Y}Installing Node.js 20...${R}"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
fi

# Install uv if not present
if ! command -v uv &>/dev/null; then
    echo -e "${Y}Installing uv...${R}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo -e "${G}  System dependencies ready${R}"

# --- Install Python dependencies ---
echo -e "${Y}[2/6] Installing Python dependencies...${R}"
cd "$PROJECT_ROOT"
uv sync 2>&1 | tail -1
echo -e "${G}  Python dependencies installed${R}"

# --- Build frontend ---
echo -e "${Y}[3/6] Building frontend...${R}"
cd "$PROJECT_ROOT/frontend"
[ ! -d node_modules ] && npm install --silent
VITE_FORCE_CLOUD=true VITE_API_BASE_URL="" npm run build --silent
echo -e "${G}  Frontend built to frontend/dist/${R}"

# --- Configure Nginx ---
echo -e "${Y}[4/6] Configuring Nginx...${R}"
sudo tee /etc/nginx/sites-available/narranexus > /dev/null << NGINX_CONF
server {
    listen 80;
    server_name _;

    # Frontend static files
    root ${PROJECT_ROOT}/frontend/dist;
    index index.html;

    # API reverse proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Increase timeouts for long-running LLM calls
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
    }

    # WebSocket reverse proxy
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 3600s;
    }

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000;
    }

    # SPA fallback: all other routes serve index.html
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Gzip
    gzip on;
    gzip_types text/plain application/json application/javascript text/css;
    gzip_min_length 1000;

    client_max_body_size 100M;
}
NGINX_CONF

sudo ln -sf /etc/nginx/sites-available/narranexus /etc/nginx/sites-enabled/narranexus
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
echo -e "${G}  Nginx configured${R}"

# --- Create systemd services ---
echo -e "${Y}[5/6] Creating systemd services...${R}"

ENV_FILE="$PROJECT_ROOT/.env"
WORK_DIR="$PROJECT_ROOT"
UV_BIN="$(which uv)"

# Create workspace directory
sudo mkdir -p "${BASE_WORKING_PATH:-/opt/narranexus/workspaces}"
sudo chown "$SERVICE_USER:$SERVICE_USER" "${BASE_WORKING_PATH:-/opt/narranexus/workspaces}"

create_service() {
    local name=$1
    local desc=$2
    local exec_cmd=$3

    sudo tee "/etc/systemd/system/narranexus-${name}.service" > /dev/null << UNIT
[Unit]
Description=NarraNexus ${desc}
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${WORK_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${exec_cmd}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
}

create_service "backend" "Backend API" \
    "${UV_BIN} run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --ws-ping-interval 30 --ws-ping-timeout 60"

create_service "mcp" "MCP Server" \
    "${UV_BIN} run python -m xyz_agent_context.module.module_runner mcp"

# Consolidated worker supervisor — ONE process runs the module poller, the job
# and message-bus triggers, and every IM channel trigger in a single event loop,
# each as a supervised backoff-restart task, sharing one package import + one DB
# pool. Replaces the old four-unit layout (poller / jobs / bus / channels). Must
# mirror scripts/dev-local.sh so cloud and local stay in lockstep.
#
# To split workers across VMs/containers for horizontal scale, run the
# supervisor with a subset selector on each host, e.g.:
#   ... run_worker_supervisor --only channels --channels lark
#   ... run_worker_supervisor --only poller,jobs,bus
create_service "workers" "Workers" \
    "${UV_BIN} run python -m xyz_agent_context.module.run_worker_supervisor"

# Remove units from previous layouts BEFORE daemon-reload. Any machine deployed
# by an earlier version of this script has enabled `narranexus-{poller,jobs,bus,
# channels}` (and, pre-#73, per-channel lark/slack/telegram/discord) with
# `Restart=always`. Without this, re-running would leave those OLD units running
# ALONGSIDE the new `workers` supervisor — double-polling instance_jobs, double
# Instance-completion handling, and two channel health servers fighting over
# 47831 + duplicate IM delivery. disable --now stops+disables; rm drops the file.
for _old in poller jobs bus channels lark slack telegram discord; do
    sudo systemctl disable --now "narranexus-${_old}" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/narranexus-${_old}.service"
done

sudo systemctl daemon-reload
echo -e "${G}  Systemd services created${R}"

# --- Start services ---
echo -e "${Y}[6/6] Starting services...${R}"
for svc in backend mcp workers; do
    sudo systemctl enable "narranexus-${svc}" --quiet
    sudo systemctl restart "narranexus-${svc}"
    echo -e "  ${G}●${R} narranexus-${svc}"
done

# Wait for backend
echo -n "  Waiting for backend"
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/health > /dev/null 2>&1; then
        echo -e " ${G}ready${R}"
        break
    fi
    echo -n "."
    sleep 1
done

echo ""
echo -e "${G}============================================================${R}"
echo -e "${G}  Deployment complete!${R}"
echo ""
echo -e "  Frontend:  ${C}http://$(curl -s ifconfig.me 2>/dev/null || echo '<your-ip>')${R}"
echo -e "  Backend:   ${C}http://127.0.0.1:8000${R}"
echo ""
echo -e "  Manage services:"
echo -e "    sudo systemctl status narranexus-backend"
echo -e "    sudo journalctl -u narranexus-backend -f"
echo -e "    sudo systemctl restart narranexus-backend"
echo ""
echo -e "  All services: narranexus-{backend,mcp,workers}"
echo -e "${G}============================================================${R}"
