#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NarraNexus — Local Development Server (tmux)
# Starts all services in a tmux session with separate windows.
# Window 0: Control panel (status + quit)
# Window 1-5: Individual services
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SESSION="nexus-dev"

# --- Platform-aware SQLite path ---
case "$(uname -s)" in
  Darwin) DB_DIR="$HOME/.narranexus" ;;
  *)      DB_DIR="$HOME/.narranexus" ;;
esac
mkdir -p "$DB_DIR"
export DATABASE_URL="sqlite:///$DB_DIR/nexus.db"

# --- Check tmux ---
if ! command -v tmux &>/dev/null; then
  echo "tmux is required. Install: brew install tmux (macOS) or apt install tmux (Linux)"
  exit 1
fi

# --- Kill existing session if running ---
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Stopping existing session '$SESSION'..."
  tmux kill-session -t "$SESSION"
  sleep 1
fi

# --- Banner ---
C="\033[36m"; G="\033[32m"; Y="\033[33m"; R="\033[0m"
echo ""
echo -e "${C}  _   _                    _   _                    ${R}"
echo -e "${C} | \\ | | __ _ _ __ _ __ __|  \\| | _____  ___   _ ___${R}"
echo -e "${C} |  \\| |/ _\` | '__| '__/ _\` | |\` |/ _ \\\\ \\\\/ / | | / __|${R}"
echo -e "${C} | |\\\\ | (_| | |  | | | (_| | |\\\\ |  __/>  <| |_| \\\\__ \\\\${R}"
echo -e "${C} |_| \\\\_|\\\\__,_|_|  |_|  \\\\__,_|_| \\\\_|\\\\___/_/\\\\_\\\\\\\\__,_|___/${R}"
echo ""
echo -e "  ${G}Local Development Server${R}"
echo -e "  Database: ${Y}$DATABASE_URL${R}"
echo ""

# --- Common env ---
SQLITE_PROXY_PORT="${SQLITE_PROXY_PORT:-8100}"
SQLITE_PROXY_URL="http://localhost:${SQLITE_PROXY_PORT}"
ENV_CMD="export DATABASE_URL='$DATABASE_URL'; export SQLITE_PROXY_URL='$SQLITE_PROXY_URL'; cd '$PROJECT_ROOT'"

# --- Create control script ---
CONTROL_SCRIPT="$PROJECT_ROOT/scripts/.control.sh"
cat > "$CONTROL_SCRIPT" << 'CTRL'
#!/usr/bin/env bash
SESSION="nexus-dev"
C="\033[36m"; G="\033[32m"; Y="\033[33m"; RED="\033[31m"; DIM="\033[2m"; R="\033[0m"

status_line() {
  local label="$1" check="$2"
  if eval "$check" 2>/dev/null; then
    printf "  ${G}●${R} %-20s\n" "$label"
  else
    printf "  ${RED}○${R} %-20s\n" "$label"
  fi
}

# Draw static parts once
clear
echo ""
echo -e "${C}  ╔═══════════════════════════════════════╗${R}"
echo -e "${C}  ║       NarraNexus Control Panel        ║${R}"
echo -e "${C}  ╚═══════════════════════════════════════╝${R}"
echo ""
echo -e "  ${Y}Service Status${R}          ${DIM}(updates every 3s)${R}"
echo ""
echo "                              "  # Backend
echo "                              "  # Frontend
echo "                              "  # MCP
echo "                              "  # Poller
echo "                              "  # Jobs
echo ""
echo -e "  ${Y}Navigation${R}"
echo ""
echo -e "  ${C}Ctrl+B N${R}  Next window       ${C}Ctrl+B 1-6${R}  Jump to service"
echo -e "  ${C}Ctrl+B P${R}  Previous window   ${C}Ctrl+B D${R}    Detach"
echo ""
echo -e "  Press ${RED}q${R} to stop all services and exit"

# Status lines start at row 8 (after header)
STATUS_ROW=8

while true; do
  # Move cursor to status area and overwrite
  printf "\033[${STATUS_ROW};0H"
  status_line "DB Proxy      :8100" "lsof -iTCP:8100 -sTCP:LISTEN -P -n >/dev/null || ss -tlnp 2>/dev/null | grep -q ':8100 '"
  status_line "Backend API   :8000" "lsof -iTCP:8000 -sTCP:LISTEN -P -n >/dev/null"
  status_line "Frontend      :5173" "lsof -iTCP:5173 -sTCP:LISTEN -P -n >/dev/null || lsof -iTCP:5174 -sTCP:LISTEN -P -n >/dev/null"
  status_line "MCP Server"          "pgrep -f 'module_runner.py mcp' >/dev/null"
  status_line "Module Poller"       "pgrep -f 'module_poller' >/dev/null"
  status_line "Job Trigger"         "pgrep -f 'job_trigger' >/dev/null"
  status_line "Bus Trigger"         "pgrep -f 'message_bus_trigger' >/dev/null"

  # Move cursor below the UI
  printf "\033[20;0H"

  if read -t 3 -n 1 key 2>/dev/null; then
    if [ "$key" = "q" ] || [ "$key" = "Q" ]; then
      printf "\033[20;0H"
      echo -e "  ${Y}Stopping all services...${R}"
      tmux kill-session -t "$SESSION" 2>/dev/null
      echo -e "  ${G}Done.${R}"
      exit 0
    fi
  fi
done
CTRL
chmod +x "$CONTROL_SCRIPT"

# --- Create tmux session with Control window ---
tmux new-session -d -s "$SESSION" -n "Control" \
  "bash '$CONTROL_SCRIPT'"

# --- SQLite Proxy (MUST start first — all other services depend on it) ---
tmux new-window -t "$SESSION" -n "DB Proxy" \
  "$ENV_CMD; export SQLITE_PROXY_PORT='$SQLITE_PROXY_PORT'; echo '=== SQLite Proxy :$SQLITE_PROXY_PORT ==='; uv run python -m xyz_agent_context.utils.sqlite_proxy_server; echo 'DB Proxy stopped. Press Enter to close.'; read"

# Wait for proxy to be ready before starting other services
sleep 3

# --- Backend ---
tmux new-window -t "$SESSION" -n "Backend" \
  "$ENV_CMD; echo '=== Backend API :8000 ==='; uv run uvicorn backend.main:app --port 8000; echo 'Backend stopped. Press Enter to close.'; read"

# --- MCP Server ---
tmux new-window -t "$SESSION" -n "MCP" \
  "$ENV_CMD; echo '=== MCP Server ==='; uv run python src/xyz_agent_context/module/module_runner.py mcp; echo 'MCP stopped. Press Enter to close.'; read"

# --- Module Poller ---
tmux new-window -t "$SESSION" -n "Poller" \
  "$ENV_CMD; echo '=== Module Poller ==='; uv run python -m xyz_agent_context.services.module_poller; echo 'Poller stopped. Press Enter to close.'; read"

# --- Job Trigger ---
tmux new-window -t "$SESSION" -n "Jobs" \
  "$ENV_CMD; echo '=== Job Trigger ==='; uv run python src/xyz_agent_context/module/job_module/job_trigger.py; echo 'Jobs stopped. Press Enter to close.'; read"

# --- Bus Trigger ---
tmux new-window -t "$SESSION" -n "BusTrigger" \
  "$ENV_CMD; echo '=== Bus Trigger ==='; uv run python -m xyz_agent_context.message_bus.message_bus_trigger; echo 'Bus Trigger stopped. Press Enter to close.'; read"

# --- Frontend ---
tmux new-window -t "$SESSION" -n "Frontend" \
  "cd '$PROJECT_ROOT/frontend'; echo '=== Frontend Dev Server ==='; npm run dev; echo 'Frontend stopped. Press Enter to close.'; read"

# --- Select Control window ---
tmux select-window -t "$SESSION:Control"

echo -e "${G}All services started in tmux session '${SESSION}'.${R}"
echo ""
echo -e "  Frontend:  ${C}http://localhost:5173${R}"
echo -e "  Backend:   ${C}http://localhost:8000${R}"
echo ""

# --- Attach ---
tmux attach -t "$SESSION"
