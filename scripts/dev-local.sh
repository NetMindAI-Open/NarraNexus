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

# --- Kill existing session and orphan processes ---
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Stopping existing session '$SESSION'..."
  tmux kill-session -t "$SESSION"
fi
# Always clean up orphan processes from a previous run
pkill -f "sqlite_proxy_server" 2>/dev/null || true
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
pkill -f "xyz_agent_context.module.module_runner mcp" 2>/dev/null || true
pkill -f "module_poller" 2>/dev/null || true
pkill -f "job_trigger" 2>/dev/null || true
pkill -f "message_bus_trigger" 2>/dev/null || true
pkill -f "run_lark_trigger" 2>/dev/null || true
pkill -f "run_slack_trigger" 2>/dev/null || true
pkill -f "run_telegram_trigger" 2>/dev/null || true
for port in 8100 8000 5173 5174 7801 7802 7803 7804 7806 7807 7808 7820 7830 7831 7832; do
  lsof -ti:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
done
sleep 1

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

# --- Ensure venv is fully synced (safety net) ---
# run.sh already calls `uv sync`, but after branch switches or lockfile
# changes the venv can be stale (uv reports "Audited" without actually
# installing new/changed deps). A second `uv sync` here is cheap (no-op
# when already up-to-date) and prevents the "Failed to spawn: uvicorn"
# error that bites every new contributor on a fresh checkout.
echo -e "${Y}Verifying Python environment...${R}"
(cd "$PROJECT_ROOT" && env -u VIRTUAL_ENV uv sync 2>&1 | tail -1)

# Quick sanity check: if uvicorn still isn't installed, abort early with
# a clear message instead of letting the Backend tmux window die silently.
if [ ! -x "$PROJECT_ROOT/.venv/bin/uvicorn" ]; then
  echo -e "${RED}ERROR: uvicorn not found in .venv after uv sync.${R}"
  echo -e "  Try: cd $PROJECT_ROOT && rm -rf .venv && uv sync"
  exit 1
fi

# --- Common env ---
SQLITE_PROXY_PORT="${SQLITE_PROXY_PORT:-8100}"
# Use 127.0.0.1 explicitly, not localhost. On macOS `localhost` resolves to
# IPv6 ::1 first, but uvicorn binds 127.0.0.1 (IPv4 only) by default —
# httpx connects to ::1, gets ECONNREFUSED, and never falls back. This bit
# every macOS contributor on a fresh checkout.
SQLITE_PROXY_URL="${SQLITE_PROXY_URL:-http://127.0.0.1:${SQLITE_PROXY_PORT}}"
# Forward narrative-related env vars set in the parent shell into each
# tmux pane. Useful for A/B experiments — set NARRATIVE_JUDGE_MODEL /
# NARRATIVE_CONTINUITY_MODEL / NARRATIVE_UPDATE_MODEL etc. before
# `bash run.sh` and they reach the backend/poller/jobs/bus workers.
NARRATIVE_ENV=""
for var in NARRATIVE_JUDGE_MODEL NARRATIVE_JUDGE_EFFORT \
           NARRATIVE_CONTINUITY_MODEL NARRATIVE_CONTINUITY_EFFORT \
           NARRATIVE_UPDATE_MODEL NARRATIVE_UPDATE_EFFORT; do
  value="${!var-}"
  if [ -n "$value" ]; then
    NARRATIVE_ENV+="export $var='$value'; "
  fi
done

ENV_CMD="export DATABASE_URL='$DATABASE_URL'; export SQLITE_PROXY_URL='$SQLITE_PROXY_URL'; ${NARRATIVE_ENV}cd '$PROJECT_ROOT'"

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

detect_frontend_url() {
  if lsof -iTCP:5173 -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo "http://localhost:5173"
  elif lsof -iTCP:5174 -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo "http://localhost:5174"
  else
    echo ""
  fi
}

draw_panel() {
  clear
  echo ""
  echo -e "${C}  ╔═══════════════════════════════════════╗${R}"
  echo -e "${C}  ║       NarraNexus Control Panel        ║${R}"
  echo -e "${C}  ╚═══════════════════════════════════════╝${R}"
  echo ""
  echo -e "  ${Y}Open in Browser${R}         ${DIM}(Ctrl/Cmd + click the link)${R}"
  echo ""
  local fe_url
  fe_url="$(detect_frontend_url)"
  if [ -n "$fe_url" ]; then
    echo -e "  ${G}▸${R} Frontend  ${C}${fe_url}${R}"
  else
    echo -e "  ${DIM}▸ Frontend  starting up... (check Frontend window)${R}"
  fi
  if lsof -iTCP:8000 -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo -e "  ${G}▸${R} Backend   ${C}http://localhost:8000${R}"
    echo -e "  ${G}▸${R} API Docs  ${C}http://localhost:8000/docs${R}"
  else
    echo -e "  ${DIM}▸ Backend   starting up... (check Backend window)${R}"
  fi
  echo ""
  echo -e "  ${Y}Service Status${R}          ${DIM}(updates every 3s)${R}"
  echo ""
  status_line "DB Proxy      :8100" "lsof -iTCP:8100 -sTCP:LISTEN -P -n >/dev/null || ss -tlnp 2>/dev/null | grep -q ':8100 '"
  status_line "Backend API   :8000" "lsof -iTCP:8000 -sTCP:LISTEN -P -n >/dev/null"
  status_line "Frontend      :5173" "lsof -iTCP:5173 -sTCP:LISTEN -P -n >/dev/null || lsof -iTCP:5174 -sTCP:LISTEN -P -n >/dev/null"
  status_line "MCP Server"          "pgrep -f 'xyz_agent_context.module.module_runner mcp' >/dev/null"
  status_line "Module Poller"       "pgrep -f 'module_poller' >/dev/null"
  status_line "Job Trigger"         "pgrep -f 'job_trigger' >/dev/null"
  status_line "Bus Trigger"         "pgrep -f 'message_bus_trigger' >/dev/null"
  status_line "Lark Trigger"        "pgrep -f 'run_lark_trigger' >/dev/null"
  status_line "Slack Trigger"       "pgrep -f 'run_slack_trigger' >/dev/null"
  status_line "Telegram Trigger"    "pgrep -f 'run_telegram_trigger' >/dev/null"
  echo ""
  echo -e "  ${Y}Navigation${R}"
  echo ""
  echo -e "  ${C}Ctrl+B N${R}  Next window       ${C}Ctrl+B 1-8${R}  Jump to service"
  echo -e "  ${C}Ctrl+B P${R}  Previous window   ${C}Ctrl+B D${R}    Detach"
  echo ""
  echo -e "  Press ${RED}q${R} to stop all services and exit"
}

draw_panel

while true; do
  if read -t 3 -n 1 key 2>/dev/null; then
    if [ "$key" = "q" ] || [ "$key" = "Q" ]; then
      echo ""
      echo -e "  ${Y}Stopping all services...${R}"
      # Kill all known NarraNexus processes BEFORE killing the tmux session.
      # tmux kill-session sends SIGHUP but some processes may ignore it.
      pkill -f "sqlite_proxy_server" 2>/dev/null || true
      pkill -f "uvicorn backend.main:app" 2>/dev/null || true
      pkill -f "xyz_agent_context.module.module_runner mcp" 2>/dev/null || true
      pkill -f "module_poller" 2>/dev/null || true
      pkill -f "job_trigger" 2>/dev/null || true
      pkill -f "message_bus_trigger" 2>/dev/null || true
      pkill -f "run_lark_trigger" 2>/dev/null || true
      pkill -f "run_slack_trigger" 2>/dev/null || true
      pkill -f "run_telegram_trigger" 2>/dev/null || true
      # Kill processes on known ports
      for port in 8100 8000 5173 5174 7801 7802 7803 7804 7806 7807 7808 7820 7830 7831 7832; do
        lsof -ti:"$port" 2>/dev/null | xargs kill 2>/dev/null || true
      done
      sleep 1
      # Force-kill any stragglers
      for port in 8100 8000 5173 5174 7801 7802 7803 7804 7806 7807 7808 7820 7830 7831 7832; do
        lsof -ti:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
      done
      echo -e "  ${G}All services stopped.${R}"
      sleep 1
      tmux kill-session -t "$SESSION" 2>/dev/null
      exit 0
    fi
  fi
  draw_panel
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
echo -n "Waiting for DB Proxy..."
for _i in $(seq 1 20); do
  if curl -sf "http://localhost:${SQLITE_PROXY_PORT}/health" >/dev/null 2>&1 || \
     lsof -iTCP:"${SQLITE_PROXY_PORT}" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo " ready"
    break
  fi
  echo -n "."
  sleep 1
done

# Use the venv's interpreter / uvicorn directly instead of `uv run`. `uv run`
# re-resolves the project + may rebuild the env, which can clobber the
# editable install (uv sync does not always re-create the .pth on every
# Python build). Using `.venv/bin/...` directly is faster AND immune to that
# clobbering behavior. See run.sh for the editable-install setup.
VENV_PY="$PROJECT_ROOT/.venv/bin/python3"
VENV_UVI="$PROJECT_ROOT/.venv/bin/uvicorn"

# --- Backend ---
tmux new-window -t "$SESSION" -n "Backend" \
  "$ENV_CMD; echo '=== Backend API :8000 ==='; DASHBOARD_BIND_HOST=127.0.0.1 '$VENV_UVI' backend.main:app --host 127.0.0.1 --port 8000 --ws-ping-interval 30 --ws-ping-timeout 60; echo 'Backend stopped. Press Enter to close.'; read"

# --- MCP Server ---
tmux new-window -t "$SESSION" -n "MCP" \
  "$ENV_CMD; echo '=== MCP Server ==='; '$VENV_PY' -m xyz_agent_context.module.module_runner mcp; echo 'MCP stopped. Press Enter to close.'; read"

# --- Module Poller ---
tmux new-window -t "$SESSION" -n "Poller" \
  "$ENV_CMD; echo '=== Module Poller ==='; '$VENV_PY' -m xyz_agent_context.services.module_poller; echo 'Poller stopped. Press Enter to close.'; read"

# --- Job Trigger ---
tmux new-window -t "$SESSION" -n "Jobs" \
  "$ENV_CMD; echo '=== Job Trigger ==='; '$VENV_PY' src/xyz_agent_context/module/job_module/job_trigger.py; echo 'Jobs stopped. Press Enter to close.'; read"

# --- Bus Trigger ---
tmux new-window -t "$SESSION" -n "BusTrigger" \
  "$ENV_CMD; echo '=== Bus Trigger ==='; '$VENV_PY' -m xyz_agent_context.message_bus.message_bus_trigger; echo 'Bus Trigger stopped. Press Enter to close.'; read"

# --- Lark Trigger ---
tmux new-window -t "$SESSION" -n "LarkTrigger" \
  "$ENV_CMD; echo '=== Lark Trigger ==='; '$VENV_PY' -m xyz_agent_context.module.lark_module.run_lark_trigger; echo 'Lark Trigger stopped. Press Enter to close.'; read"

# --- Slack Trigger ---
tmux new-window -t "$SESSION" -n "SlackTrigger" \
  "$ENV_CMD; echo '=== Slack Trigger ==='; uv run python -m xyz_agent_context.module.slack_module.run_slack_trigger; echo 'Slack Trigger stopped. Press Enter to close.'; read"

# --- Telegram Trigger ---
tmux new-window -t "$SESSION" -n "TelegramTrigger" \
  "$ENV_CMD; echo '=== Telegram Trigger ==='; uv run python -m xyz_agent_context.module.telegram_module.run_telegram_trigger; echo 'Telegram Trigger stopped. Press Enter to close.'; read"

# --- Frontend ---
tmux new-window -t "$SESSION" -n "Frontend" \
  "cd '$PROJECT_ROOT/frontend'; echo '=== Frontend Dev Server ==='; npm run dev; echo 'Frontend stopped. Press Enter to close.'; read"

# --- Select Control window ---
tmux select-window -t "$SESSION:Control"

# --- Readiness verification (iron rule #7: mirror the dmg's process_manager
# readiness gate). Wait for the port-bound services and confirm the required
# portless workers are alive; surface a clear error + log path for any that
# didn't come up, instead of cheerfully printing "All services started" while
# the app would fail every request with "Connection failed".
RED="\033[31m"
LOG_BASE="$HOME/.narranexus/logs"
logpath() { echo "${LOG_BASE}/$1/$1_$(date +%Y%m%d).log"; }

echo -n "Waiting for Backend API :8000..."
backend_ok=false
for _i in $(seq 1 45); do
  if curl -sf "http://127.0.0.1:8000/docs" >/dev/null 2>&1 \
     || lsof -iTCP:8000 -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo " ready"; backend_ok=true; break
  fi
  echo -n "."; sleep 1
done
[ "$backend_ok" = true ] || echo " NOT READY"

# Give the portless workers a moment to either come up or crash on import.
sleep 2

# Newline-delimited accumulator (NOT a bash array — macOS ships bash 3.2 where
# `"${arr[@]}"` on an empty array trips `set -u`).
failed=""
add_fail() { failed="${failed}$1"$'\n'; }
[ "$backend_ok" = true ] || add_fail "Backend API|backend|HTTP :8000 never became reachable"
{ lsof -iTCP:8100 -sTCP:LISTEN -P -n >/dev/null 2>&1 || ss -tlnp 2>/dev/null | grep -q ':8100 '; } \
  || add_fail "SQLite Proxy|sqlite_proxy|:8100 never came up"
pgrep -f 'xyz_agent_context.module.module_runner mcp' >/dev/null 2>&1 || add_fail "MCP Server|mcp|process not running (crashed on startup?)"
pgrep -f 'module_poller' >/dev/null 2>&1 || add_fail "Module Poller|poller|process not running (crashed on startup?)"
pgrep -f 'job_trigger' >/dev/null 2>&1 || add_fail "Job Trigger|job_trigger|process not running (crashed on startup?)"
pgrep -f 'message_bus_trigger' >/dev/null 2>&1 || add_fail "Bus Trigger|message_bus_trigger|process not running (crashed on startup?)"

if [ -n "$failed" ]; then
  echo ""
  echo -e "  ${RED}⚠ Some REQUIRED services did not start:${R}"
  printf "%s" "$failed" | while IFS='|' read -r label sid reason; do
    [ -z "$label" ] && continue
    echo -e "    ${RED}✗${R} ${label} — ${reason}"
    echo -e "       log:  $(logpath "$sid")"
    echo -e "       live: tmux attach -t ${SESSION}  (window: ${label})"
  done || true
  echo -e "  ${Y}Until fixed, the app will show \"Connection failed\" on user/login.${R}"
  echo ""
else
  echo -e "  ${G}✓ Required services healthy: proxy, backend, mcp, poller, jobs, bus.${R}"
fi

echo -e "${G}All services started in tmux session '${SESSION}'.${R}"
echo ""
echo -e "  Frontend:  ${C}http://localhost:5173${R}"
echo -e "  Backend:   ${C}http://localhost:8000${R}"
echo ""

# --- Attach ---
# tmux attach needs a real interactive terminal. Three conditions must hold:
#   1. stdin and stdout are TTYs ([ -t 0 ] && [ -t 1 ])
#   2. TERM is set to something other than "dumb"
#   3. tmux itself thinks attach is viable (probed via a dry-run below)
# In non-interactive contexts (CI, agent shells, Claude Code's Bash tool),
# `tmux attach` crashes with "open terminal failed: not a terminal". In that
# case we leave the session detached and print clear manual instructions.
_can_attach=false
if [ -t 0 ] && [ -t 1 ] && [ -n "${TERM:-}" ] && [ "${TERM:-}" != "dumb" ]; then
  # Final probe: try a no-op tmux command against the current $TERM. If tmux
  # can't open a terminal here, the attach would fail the same way.
  if tmux display-message -p "" >/dev/null 2>&1; then
    _can_attach=true
  fi
fi

if [ "$_can_attach" = "true" ]; then
  exec tmux attach -t "$SESSION"
else
  echo -e "  ${Y}No interactive terminal — services running in detached tmux session.${R}"
  echo -e "  Attach manually:  ${C}tmux attach -t ${SESSION}${R}"
  echo -e "  Stop all:         ${C}bash $(dirname "$SCRIPT_DIR")/run.sh stop${R}"
  echo ""
fi
