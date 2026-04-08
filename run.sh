#!/usr/bin/env bash
# ============================================================================
#   _   _                    _   _
#  | \ | | __ _ _ __ _ __ __|  \| | _____  ___   _ ___
#  |  \| |/ _` | '__| '__/ _` | |` |/ _ \ \/ / | | / __|
#  | |\ | (_| | |  | | | (_| | |\ |  __/>  <| |_| \__ \
#  |_| \_|\__,_|_|  |_|  \__,_|_| \_|\___/_/\_\\__,_|___/
#
#  NarraNexus — Intelligent Agent Platform
# ============================================================================
#
#  Usage:
#    bash run.sh          Start all services (backend + frontend)
#    bash run.sh stop     Stop all NarraNexus processes
#    bash run.sh status   Show service status
#    bash run.sh build    Build desktop app (DMG)
#
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
C="\033[36m"; G="\033[32m"; Y="\033[33m"; R="\033[0m"; RED="\033[31m"

# --- Helpers ---

status() {
  echo ""
  echo -e "${C}Service Status${R}"
  echo ""
  local services=("8100:DB Proxy" "8000:Backend API" "5173:Frontend" "7801:MCP Server")
  for entry in "${services[@]}"; do
    local port="${entry%%:*}"
    local name="${entry#*:}"
    if lsof -iTCP:"$port" -sTCP:LISTEN -P -n &>/dev/null 2>&1 || \
       ss -tlnp 2>/dev/null | grep -q ":${port} "; then
      echo -e "  ${G}●${R} ${name} (port ${port})"
    else
      echo -e "  ${RED}○${R} ${name} (port ${port})"
    fi
  done
  echo ""
}

stop_all() {
  echo -e "${Y}Stopping NarraNexus services...${R}"
  # Kill tmux session if running
  tmux kill-session -t nexus-dev 2>/dev/null || true
  # Kill processes on known ports
  for port in 8100 8000 5173 5174 7801 7802 7803 7804 7805; do
    lsof -ti:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
  done
  # Kill known process patterns
  pkill -f "sqlite_proxy_server" 2>/dev/null || true
  pkill -f "uvicorn backend.main:app" 2>/dev/null || true
  pkill -f "module_runner.py mcp" 2>/dev/null || true
  pkill -f "module_poller" 2>/dev/null || true
  pkill -f "job_trigger" 2>/dev/null || true
  echo -e "${G}All services stopped.${R}"
}

check_deps() {
  local missing=()
  command -v uv  &>/dev/null || missing+=("uv")
  command -v node &>/dev/null || missing+=("node")
  if [ ${#missing[@]} -gt 0 ]; then
    echo -e "${RED}Missing dependencies: ${missing[*]}${R}"
    echo ""
    echo "  Install uv:   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "  Install node:  https://nodejs.org/"
    echo ""
    exit 1
  fi
}

# --- Main ---

case "${1:-}" in
  stop)
    stop_all
    ;;
  status)
    status
    ;;
  build)
    exec "$SCRIPT_DIR/scripts/build-desktop.sh"
    ;;
  *)
    check_deps

    # Install frontend deps if needed
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
      echo -e "${Y}Installing frontend dependencies...${R}"
      (cd "$SCRIPT_DIR/frontend" && npm install)
    fi

    # Sync Python deps
    echo -e "${Y}Syncing Python dependencies...${R}"
    uv sync 2>&1 | tail -1

    # Start everything
    exec "$SCRIPT_DIR/scripts/dev-local.sh"
    ;;
esac
