#!/usr/bin/env bash
# ============================================================================
# NarraNexus Control Panel — runs inside tmux, provides status view and quit
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVERMEMOS_DIR="${PROJECT_ROOT}/.evermemos"
TMUX_SESSION="xyz-dev"

# Colors
BOLD='\033[1m'
DIM='\033[2m'
UNDERLINE='\033[4m'
RESET='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
G1='\033[38;5;39m'
G2='\033[38;5;38m'
G3='\033[38;5;44m'
G4='\033[38;5;43m'
G5='\033[38;5;49m'
G6='\033[38;5;48m'
BG_GREEN='\033[42m'
BG_RED='\033[41m'

# Docker permission detection
DOCKER_CMD="docker"
detect_docker_permission() {
    if ! command -v docker &>/dev/null; then return; fi
    if docker info &>/dev/null 2>&1; then
        DOCKER_CMD="docker"
    elif sudo docker info &>/dev/null 2>&1; then
        DOCKER_CMD="sudo docker"
    fi
}

# Docker Compose command detection (auto-adapts sudo)
detect_compose_cmd() {
    detect_docker_permission
    if $DOCKER_CMD compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="$DOCKER_CMD compose"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        COMPOSE_CMD=""
    fi
}

# OS detection
OS_TYPE="$(uname -s)"  # Darwin = macOS, Linux = Linux

# Port status detection (cross-platform: macOS uses lsof, Linux uses ss)
is_port_up() {
    local port="$1"
    if [ "$OS_TYPE" = "Darwin" ]; then
        lsof -iTCP:"$port" -sTCP:LISTEN -P -n &>/dev/null
    else
        ss -tlnp 2>/dev/null | grep -q ":${port} "
    fi
}

show_dashboard() {
    clear
    echo ""
    echo -e "${G1}    ███╗   ██╗${G2} █████╗ ${G3}██████╗ ${G4}██████╗  ${G5} █████╗ ${RESET}"
    echo -e "${G1}    ████╗  ██║${G2}██╔══██╗${G3}██╔══██╗${G4}██╔══██╗${G5}██╔══██╗${RESET}"
    echo -e "${G1}    ██╔██╗ ██║${G2}███████║${G3}██████╔╝${G4}██████╔╝${G5}███████║${RESET}"
    echo -e "${G1}    ██║╚██╗██║${G2}██╔══██║${G3}██╔══██╗${G4}██╔══██╗${G5}██╔══██║${RESET}"
    echo -e "${G1}    ██║ ╚████║${G2}██║  ██║${G3}██║  ██║${G4}██║  ██║${G5}██║  ██║${RESET}"
    echo -e "${G1}    ╚═╝  ╚═══╝${G2}╚═╝  ╚═╝${G3}╚═╝  ╚═╝${G4}╚═╝  ╚═╝${G5}╚═╝  ╚═╝${RESET}"
    echo ""
    echo -e "${G3}    ███╗   ██╗${G4}███████╗${G5}██╗  ██╗${G6}██╗   ██╗███████╗${RESET}"
    echo -e "${G3}    ████╗  ██║${G4}██╔════╝${G5}╚██╗██╔╝${G6}██║   ██║██╔════╝${RESET}"
    echo -e "${G3}    ██╔██╗ ██║${G4}█████╗  ${G5} ╚███╔╝ ${G6}██║   ██║███████╗${RESET}"
    echo -e "${G3}    ██║╚██╗██║${G4}██╔══╝  ${G5} ██╔██╗ ${G6}██║   ██║╚════██║${RESET}"
    echo -e "${G3}    ██║ ╚████║${G4}███████╗${G5}██╔╝ ██╗${G6}╚██████╔╝███████║${RESET}"
    echo -e "${G3}    ╚═╝  ╚═══╝${G4}╚══════╝${G5}╚═╝  ╚═╝${G6} ╚═════╝ ╚══════╝${RESET}"
    echo ""

    # ── Access URLs (prominent display) ──
    echo -e "  ${DIM}  ══════════════════════════════════════════════${RESET}"
    if is_port_up 5173; then
        echo -e "  ${BOLD}  ${GREEN}▶${RESET}${BOLD}  Frontend:  ${UNDERLINE}${WHITE}http://localhost:5173${RESET}"
    else
        echo -e "  ${BOLD}  ${YELLOW}◷${RESET}${BOLD}  Frontend:  ${DIM}Starting...${RESET}"
    fi
    if is_port_up 8000; then
        echo -e "  ${BOLD}  ${GREEN}▶${RESET}${BOLD}  Backend:   ${UNDERLINE}${WHITE}http://localhost:8000${RESET}${DIM}/docs${RESET}"
    else
        echo -e "  ${BOLD}  ${YELLOW}◷${RESET}${BOLD}  Backend:   ${DIM}Starting...${RESET}"
    fi
    echo -e "  ${DIM}  ══════════════════════════════════════════════${RESET}"
    echo ""

    # ── Infrastructure status ──
    echo -e "  ${BOLD}  Infrastructure${RESET}"
    local infra_items=("MySQL:3306" "MongoDB:27017" "Elasticsearch:19200" "Redis:6379" "Milvus:19530" "EverMemOS:1995")
    printf "    "
    for item in "${infra_items[@]}"; do
        local name="${item%%:*}"
        local port="${item##*:}"
        if is_port_up "$port"; then
            printf "${GREEN}●${RESET} %-14s" "$name"
        else
            printf "${RED}○${RESET} %-14s" "$name"
        fi
    done
    echo ""
    echo ""

    # ── Application services status ──
    echo -e "  ${BOLD}  Application Services${RESET}"
    local app_items=("Frontend:5173" "FastAPI:8000" "MCP:7801" "NexusMatrix:8953" "JobTrigger:-" "MatrixTrig:-" "Poller:-")
    printf "    "
    for item in "${app_items[@]}"; do
        local name="${item%%:*}"
        local port="${item##*:}"
        if [ "$port" = "-" ]; then
            # Background services without ports, check if tmux window exists
            printf "${GREEN}●${RESET} %-14s" "$name"
        elif is_port_up "$port"; then
            printf "${GREEN}●${RESET} %-14s" "$name"
        else
            printf "${YELLOW}◷${RESET} %-14s" "$name"
        fi
    done
    echo ""
    echo ""

    # ── Controls ──
    echo -e "  ${DIM}  ──────────────────────────────────────────────${RESET}"
    echo -e "    ${WHITE}Ctrl-b + n/p${RESET}  Switch windows to view logs"
    echo ""
    echo -e "    ${WHITE}[r]${RESET}  Refresh status    ${RED}${BOLD}[q]${RESET} ${RED}Stop all services and exit${RESET}"
    echo -e "  ${DIM}  ──────────────────────────────────────────────${RESET}"
    echo ""
}

do_stop_all() {
    echo ""
    echo -e "  ${YELLOW}Stopping all services...${RESET}"
    echo ""

    # Stop application processes: SIGTERM first, then SIGKILL for survivors
    local patterns=(
        "uvicorn.*backend.main"      # FastAPI backend (port 8000)
        "uvicorn.*1995"              # EverMemOS Web
        "module_runner.py.*mcp"      # MCP server (port 7801)
        "module_poller"              # ModulePoller
        "job_trigger.py"             # Job trigger
        "npm.*dev.*5173"             # Frontend dev server
        "vite.*5173"                 # Vite (frontend actual process)
        "node.*vite"                 # Vite node process
        "nexus_matrix.main"          # NexusMatrix Server (port 8953)
        "matrix_trigger"             # MatrixTrigger (message polling)
    )
    for pat in "${patterns[@]}"; do
        pkill -f "$pat" 2>/dev/null
    done

    # Wait briefly for graceful shutdown, then SIGKILL any survivors
    sleep 2
    for pat in "${patterns[@]}"; do
        if pgrep -f "$pat" &>/dev/null; then
            pkill -9 -f "$pat" 2>/dev/null
        fi
    done
    echo -e "  ${GREEN}✓${RESET} Application processes stopped"

    # Stop Docker containers
    detect_compose_cmd
    if [ -n "${COMPOSE_CMD:-}" ]; then
        # Stop project MySQL
        if [ -f "${PROJECT_ROOT}/docker-compose.yaml" ]; then
            cd "${PROJECT_ROOT}"
            $COMPOSE_CMD down 2>/dev/null
            echo -e "  ${GREEN}✓${RESET} MySQL Docker stopped"
        fi
        # Stop EverMemOS
        if [ -d "${EVERMEMOS_DIR}" ]; then
            cd "${EVERMEMOS_DIR}"
            $COMPOSE_CMD down 2>/dev/null
            echo -e "  ${GREEN}✓${RESET} EverMemOS Docker containers stopped"
        fi
        cd "$PROJECT_ROOT"
    fi

    echo ""
    echo -e "  ${GREEN}✓${RESET} All stopped. Closing tmux in 2 seconds..."
    sleep 2

    # Close tmux session
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
}

# If tmux is killed directly (not via 'q'), SIGHUP is sent — run cleanup
trap 'do_stop_all; exit 0' SIGHUP SIGTERM SIGINT

# === Initial startup: wait for services to be ready ===
# control.sh starts as the first window; other services are still initializing.
# Auto-refresh until ready.
AUTO_REFRESH=true

# === Main loop ===
while true; do
    show_dashboard

    if [ "$AUTO_REFRESH" = true ]; then
        # Auto-refresh mode: refresh every 3 seconds until all core services are ready
        if is_port_up 5173 && is_port_up 8000 && is_port_up 7801 && is_port_up 8953; then
            AUTO_REFRESH=false
            continue  # Refresh one last time to show all-green status
        fi
        # Wait 3 seconds; if user presses a key, respond immediately
        read -rsn1 -t 3 key || true
        case "${key:-}" in
            r|R) continue ;;
            q|Q) do_stop_all; exit 0 ;;
            "")  continue ;;  # Timeout, auto-refresh
        esac
    else
        read -rsn1 key
        case "$key" in
            r|R) continue ;;
            q|Q) do_stop_all; exit 0 ;;
        esac
    fi
done
