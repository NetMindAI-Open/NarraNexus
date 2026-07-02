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
#
#  Desktop DMG builds are produced by the GitHub Actions release
#  workflow on tag push (see .github/workflows/), not by this script.
#
# ============================================================================

set -uo pipefail

# Clear any external VIRTUAL_ENV (e.g. pyenv) that interferes with uv's .venv
unset VIRTUAL_ENV 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
C="\033[36m"; G="\033[32m"; Y="\033[33m"; R="\033[0m"; RED="\033[31m"

# --- Helpers ---

status() {
  echo ""
  echo -e "${C}Service Status${R}"
  echo ""
  local services=(
    "8100:DB Proxy" "8000:Backend API" "5173:Frontend"
    "7801:MCP Awareness" "7802:MCP SocialNetwork" "7803:MCP Job" "7804:MCP Chat"
    "7806:MCP Skill" "7807:MCP CommonTools" "7808:MCP BasicInfo" "7820:MCP MessageBus"
    "7830:Lark Trigger" "7831:Slack Trigger" "7832:Telegram Trigger" "7834:Discord Trigger")
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
  for port in 8100 8000 5173 5174 7801 7802 7803 7804 7806 7807 7808 7820 7830 7831 7832 7834; do
    lsof -ti:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
  done
  # Kill known process patterns
  pkill -f "sqlite_proxy_server" 2>/dev/null || true
  pkill -f "uvicorn backend.main:app" 2>/dev/null || true
  pkill -f "xyz_agent_context.module.module_runner mcp" 2>/dev/null || true
  pkill -f "module_poller" 2>/dev/null || true
  pkill -f "job_trigger" 2>/dev/null || true
  pkill -f "message_bus_trigger" 2>/dev/null || true
  pkill -f "run_lark_trigger" 2>/dev/null || true
  pkill -f "run_slack_trigger" 2>/dev/null || true
  pkill -f "run_telegram_trigger" 2>/dev/null || true
  pkill -f "run_wechat_trigger" 2>/dev/null || true
  pkill -f "run_narramessenger_trigger" 2>/dev/null || true
  pkill -f "run_discord_trigger" 2>/dev/null || true
  echo -e "${G}All services stopped.${R}"
}

check_deps() {
  # uv: auto-install if missing.
  # The official installer is a curl-piped shell script that drops the
  # binary at ~/.local/bin/uv — strictly user-level, no sudo. We add
  # ~/.local/bin to the current shell's PATH so the rest of this run
  # can find it; a one-line export hint covers future sessions.
  if ! command -v uv &>/dev/null; then
    echo -e "${Y}uv not found — installing automatically (user-level, no sudo)...${R}"
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
      echo -e "${RED}uv installer failed.${R}"
      echo "  Manual install: curl -LsSf https://astral.sh/uv/install.sh | sh"
      exit 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
      echo -e "${RED}uv installed but not on \$PATH.${R}"
      echo "  Add this line to your shell rc (~/.zshrc or ~/.bashrc):"
      echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
      echo "  Then restart the shell and re-run: bash run.sh"
      exit 1
    fi
    echo -e "${G}uv installed.${R}"
  fi

  # node: ask the user to install it. Cross-platform sudo handling
  # for Node is too risky to do silently, so we just print the right
  # one-liner for the detected platform and exit.
  if ! command -v node &>/dev/null; then
    echo -e "${RED}Node.js not found.${R}"
    echo ""
    echo "  Install command for your platform:"
    case "$(uname -s)" in
      Linux*)
        if grep -qi microsoft /proc/version 2>/dev/null; then
          echo -e "    ${C}sudo apt-get update && sudo apt-get install -y nodejs npm${R}  (WSL2)"
        elif command -v apt-get &>/dev/null; then
          echo -e "    ${C}sudo apt-get update && sudo apt-get install -y nodejs npm${R}  (Debian / Ubuntu)"
        elif command -v dnf &>/dev/null; then
          echo -e "    ${C}sudo dnf install -y nodejs npm${R}  (Fedora)"
        elif command -v pacman &>/dev/null; then
          echo -e "    ${C}sudo pacman -S --noconfirm nodejs npm${R}  (Arch)"
        else
          echo -e "    Use your distro's package manager, or install nvm:"
          echo -e "    ${C}curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash && nvm install 20${R}"
        fi
        ;;
      Darwin*)
        if command -v brew &>/dev/null; then
          echo -e "    ${C}brew install node${R}  (macOS, Homebrew)"
        else
          echo -e "    Install Homebrew first (https://brew.sh/), then:"
          echo -e "    ${C}brew install node${R}"
        fi
        ;;
      *)
        echo "    Download from https://nodejs.org/"
        ;;
    esac
    echo ""
    echo "  Then re-run: bash run.sh"
    exit 1
  fi

  # Install or update lark-cli (optional — only needed for Lark/Feishu).
  # The previous version silently piped `npm install` output to tail and had
  # no timeout, so a slow npm registry, EACCES on a system-wide install, or
  # blocked network would hang "Installing lark-cli..." forever with no
  # feedback. Three safeties now:
  #   1. Hard timeout (120s) so we never wedge startup.
  #   2. Output streams live so the user sees progress, not a frozen line.
  #   3. If install fails/times out we warn and continue — Lark features
  #      degrade gracefully; the rest of NarraNexus still works.
  _LARK_CLI_MIN="1.0.12"
  _LARK_CLI_TIMEOUT=120

  _try_install_lark_cli() {
    local action="$1"  # "Installing" or "Updating" (display label, pre-capitalized
    # — avoids bash 3.2 lacking ${var^})
    echo -e "${Y}${action} lark-cli (timeout ${_LARK_CLI_TIMEOUT}s)...${R}"
    # Use a subshell + background + wait-with-timeout pattern. `timeout`
    # isn't on stock macOS; this works everywhere with just sh primitives.
    (npm install -g @larksuite/cli) &
    local npm_pid=$!
    local elapsed=0
    while kill -0 "$npm_pid" 2>/dev/null; do
      if [ "$elapsed" -ge "$_LARK_CLI_TIMEOUT" ]; then
        echo -e "${RED}npm install hung > ${_LARK_CLI_TIMEOUT}s — killing.${R}"
        kill -9 "$npm_pid" 2>/dev/null
        wait "$npm_pid" 2>/dev/null
        return 124
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done
    wait "$npm_pid"
    return $?
  }

  _warn_lark_skipped() {
    echo -e "${Y}⚠ lark-cli not available — Lark/Feishu features will be disabled.${R}"
    echo "  Common causes + fixes:"
    echo "    • Slow registry (China users): npm config set registry https://registry.npmmirror.com"
    echo "    • Permission denied: use nvm (https://github.com/nvm-sh/nvm) or sudo"
    echo "    • Network blocked: check your connection to registry.npmjs.org"
    echo "  Then retry: npm install -g @larksuite/cli"
    echo ""
  }

  # Install Claude Code CLI (@anthropic-ai/claude-code). HARD dependency:
  # claude_agent_sdk spawns this binary every chat turn, so if it's absent
  # the agent loop fails immediately. Unlike lark-cli we do not degrade
  # gracefully — we exit.
  _CLAUDE_CLI_TIMEOUT=180

  _try_install_claude_cli() {
    local action="$1"
    echo -e "${Y}${action} @anthropic-ai/claude-code (timeout ${_CLAUDE_CLI_TIMEOUT}s)...${R}"
    (npm install -g @anthropic-ai/claude-code) &
    local npm_pid=$!
    local elapsed=0
    while kill -0 "$npm_pid" 2>/dev/null; do
      if [ "$elapsed" -ge "$_CLAUDE_CLI_TIMEOUT" ]; then
        echo -e "${RED}npm install hung > ${_CLAUDE_CLI_TIMEOUT}s — killing.${R}"
        kill -9 "$npm_pid" 2>/dev/null
        wait "$npm_pid" 2>/dev/null
        return 124
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done
    wait "$npm_pid"
    return $?
  }

  if ! command -v claude &>/dev/null; then
    if ! _try_install_claude_cli "Installing"; then
      echo -e "${RED}Failed to install @anthropic-ai/claude-code — this is a HARD dependency.${R}"
      echo ""
      echo "  claude_agent_sdk (our Python Agent framework) spawns the \`claude\`"
      echo "  binary every chat turn. Without it nothing works."
      echo ""
      echo "  Common fixes:"
      echo "    • Slow registry (China): npm config set registry https://registry.npmmirror.com"
      echo "    • Permission denied: use nvm (https://github.com/nvm-sh/nvm) or sudo"
      echo "    • Network blocked: check connection to registry.npmjs.org"
      echo "  Then retry: npm install -g @anthropic-ai/claude-code"
      echo ""
      exit 1
    fi
    # Post-install PATH verification (same class of bug as lark-cli below).
    if ! command -v claude &>/dev/null; then
      _npm_prefix=$(npm config get prefix 2>/dev/null || echo "")
      if [ -n "$_npm_prefix" ] && [ -x "$_npm_prefix/bin/claude" ]; then
        echo -e "${RED}claude installed at $_npm_prefix/bin but not on \$PATH.${R}"
        echo "  Add to your shell rc: export PATH=\"$_npm_prefix/bin:\$PATH\""
        echo "  Then restart the shell and retry bash run.sh."
        exit 1
      fi
      echo -e "${RED}claude install reported success but binary is nowhere to be found.${R}"
      exit 1
    fi
  fi

  if ! command -v lark-cli &>/dev/null; then
    if ! _try_install_lark_cli "Installing"; then
      _warn_lark_skipped
    fi
  else
    _lark_ver=$(lark-cli --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "0.0.0")
    if [ "$(printf '%s\n' "${_LARK_CLI_MIN}" "$_lark_ver" | sort -V | head -1)" != "${_LARK_CLI_MIN}" ]; then
      if ! _try_install_lark_cli "Updating"; then
        echo -e "${Y}⚠ lark-cli update failed; continuing with ${_lark_ver}.${R}"
      fi
    fi
  fi

  # Post-install / post-upgrade verification. npm can "succeed" but leave
  # the binary outside $PATH (classic ~/.npm-global/bin not exported case).
  if ! command -v lark-cli &>/dev/null; then
    _npm_prefix=$(npm config get prefix 2>/dev/null || echo "")
    if [ -n "$_npm_prefix" ] && [ -x "$_npm_prefix/bin/lark-cli" ]; then
      echo -e "${Y}lark-cli installed at $_npm_prefix/bin but missing from \$PATH.${R}"
      echo "  Add to your shell rc: export PATH=\"$_npm_prefix/bin:\$PATH\""
      echo ""
    fi
  fi

  # Install Lark CLI Skills (the knowledge packs the `lark_skill` MCP tool
  # serves to the Agent — SKILL.md indexes + references/, routes/, scenes/
  # subdirs). Without these, `lark_skill(...)` returns "not found" and the
  # Agent has to trial-and-error every lark-cli command.
  #
  # Mirror the Docker install: `HOME=$HOME npx skills add larksuite/cli -y -g`
  # lands the files at ~/.agents/skills/lark-*/ with a symlink at
  # ~/.claude/skills/lark-*/. Wrap in the same timeout / graceful-degrade
  # pattern as lark-cli itself so a stalled npx registry doesn't wedge startup.
  _LARK_SKILLS_TIMEOUT=180

  _try_install_lark_skills() {
    echo -e "${Y}Installing Lark CLI Skills (timeout ${_LARK_SKILLS_TIMEOUT}s)...${R}"
    (HOME="$HOME" npx skills add larksuite/cli -y -g 2>&1 | tail -3) &
    local npx_pid=$!
    local elapsed=0
    while kill -0 "$npx_pid" 2>/dev/null; do
      if [ "$elapsed" -ge "$_LARK_SKILLS_TIMEOUT" ]; then
        echo -e "${RED}npx skills install hung > ${_LARK_SKILLS_TIMEOUT}s — killing.${R}"
        kill -9 "$npx_pid" 2>/dev/null
        wait "$npx_pid" 2>/dev/null
        return 124
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done
    wait "$npx_pid"
    return $?
  }

  if ! ls ~/.agents/skills/lark-shared/SKILL.md &>/dev/null 2>&1 \
     && ! ls ~/.claude/skills/lark-shared/SKILL.md &>/dev/null 2>&1; then
    if ! _try_install_lark_skills; then
      echo -e "${Y}⚠ Lark skill install failed/timed out — `lark_skill(...)` MCP tool will return 'not found'. Lark/Feishu features degrade to runtime help (`<domain> +<cmd> --help`).${R}"
      echo "  Retry later: HOME=\$HOME npx skills add larksuite/cli -y -g"
      echo ""
    fi
  fi

  # Check Python version (>=3.13 required)
  local py_version
  py_version=$(uv python find 2>/dev/null | xargs -I{} {} -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
  if [ -n "$py_version" ]; then
    local major minor
    major="${py_version%%.*}"
    minor="${py_version#*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 13 ]; }; then
      echo -e "${RED}Python >= 3.13 is required (found $py_version).${R}"
      echo "  Install: uv python install 3.13"
      exit 1
    fi
  fi

  # Optional: lark-cli (only needed for Lark/Feishu integration)
  if ! command -v lark-cli &>/dev/null; then
    echo -e "${Y}Note: lark-cli not found. Lark/Feishu features will not work.${R}"
    echo -e "  Install: ${C}npm install -g @larksuite/cli${R}"
    echo ""
  fi
}

# --- Main ---

run_container_mode() {
  # ------------------------------------------------------------------
  # Manyfold container mode — single-process group (no tmux), all logs
  # to stdout for `docker logs`/`kubectl logs`. Backend runs in
  # foreground (PID 1 of the container effective process).
  #
  # Activated by env: RUNTIME_MODE=container (set by docker/manyfold
  # Dockerfile). Inert outside containers.
  # ------------------------------------------------------------------
  echo -e "${C}NarraNexus container mode — starting services${R}"

  # Defaults that make the volume layout sane regardless of where the
  # image is built. The Dockerfile may pre-set these; respect overrides.
  export BASE_WORKING_PATH="${BASE_WORKING_PATH:-/data/workspaces}"
  export NEXUS_LOG_DIR="${NEXUS_LOG_DIR:-/data/logs}"
  export DATABASE_URL="${DATABASE_URL:-sqlite:////data/nexus.db}"
  export DASHBOARD_BIND_HOST="${DASHBOARD_BIND_HOST:-0.0.0.0}"
  # Analytics surface label: the container image is the hosted/server form
  # factor → "cloud" (routes to NullSink this phase). Explicit override wins.
  export NARRA_SURFACE="${NARRA_SURFACE:-cloud}"

  mkdir -p "$BASE_WORKING_PATH" "$NEXUS_LOG_DIR" /data
  mkdir -p "$(dirname /data/nexus.db)" || true

  # 1. sqlite_proxy (only when DATABASE_URL is sqlite-ish)
  if [[ "${DATABASE_URL}" == sqlite* ]]; then
    "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.utils.sqlite_proxy_server &
    SQLITE_PID=$!
    # Wait up to 30s for :8100
    for i in {1..30}; do
      if (echo > /dev/tcp/127.0.0.1/8100) >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    # MUST export so the child processes below (MCP runner, module poller,
    # job/bus/IM triggers, backend) route their writes through the proxy.
    # Without this, every child opens its own SQLite connection to the
    # same file and concurrent writes deadlock with "database is locked"
    # (the proxy was started but nobody talks to it). scripts/dev-local.sh
    # exports the same var for the local-development path.
    export SQLITE_PROXY_URL="${SQLITE_PROXY_URL:-http://127.0.0.1:8100}"
  fi

  # 2. MCP module runner
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.module_runner mcp &
  # 3. Module poller
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.services.module_poller &
  # 4. Job trigger
  "$SCRIPT_DIR/.venv/bin/python3" src/xyz_agent_context/module/job_module/job_trigger.py &
  # 5. Message bus trigger
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.message_bus.message_bus_trigger &
  # 5b. Discord channel trigger (Gateway receive → AgentRuntime). dev-local.sh
  #     already launches this; run.sh must match it (binding rule #7).
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.discord_module.run_discord_trigger &

  # 6. IM channel triggers (inbound long-poll). message_bus_trigger
  #    deliberately defers IM channels to these dedicated processes, so
  #    without them inbound Lark / Slack / Telegram messages are never
  #    received — outbound still works because it goes straight through the
  #    module, not a trigger (the asymmetry behind issue #54). Mirrors the
  #    dev stack (scripts/dev-local.sh). Each trigger no-ops when no channel
  #    of its kind is bound, so starting all three unconditionally is safe.
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.lark_module.run_lark_trigger &
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.slack_module.run_slack_trigger &
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.telegram_module.run_telegram_trigger &
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.wechat_module.run_wechat_trigger &
  "$SCRIPT_DIR/.venv/bin/python3" -m xyz_agent_context.module.narramessenger_module.run_narramessenger_trigger &

  # 7. Backend — foreground (PID 1 effective). Manyfold expects 0.0.0.0:8000.
  exec "$SCRIPT_DIR/.venv/bin/python3" -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --ws-ping-interval 30 \
    --ws-ping-timeout 60
}

# Container detection MUST run before the normal case so docker/manyfold
# Dockerfile CMD ["bash","run.sh"] short-circuits the deps-check path
# (deps are baked into the image; rechecking at every container start
# wastes 5-10s).
if [ "${RUNTIME_MODE:-}" = "container" ] || [ "${IN_CONTAINER:-}" = "1" ]; then
  run_container_mode
  exit $?
fi

case "${1:-}" in
  stop)
    stop_all
    ;;
  status)
    status
    ;;
  *)
    check_deps

    # Install frontend deps if needed. Re-run `npm ci` not only when
    # node_modules is missing, but also when the lockfile CHANGED since the last
    # install — otherwise pulling a branch that ADDS a dependency (e.g. crypto-js
    # in the NetMind-login work) leaves node_modules stale and Vite fails to
    # resolve the new import (observed as a broken login page after `git pull`).
    # npm writes node_modules/.package-lock.json on install; compare against it.
    _fe="$SCRIPT_DIR/frontend"
    if [ ! -d "$_fe/node_modules" ] \
       || [ ! -f "$_fe/node_modules/.package-lock.json" ] \
       || [ "$_fe/package-lock.json" -nt "$_fe/node_modules/.package-lock.json" ]; then
      echo -e "${Y}Installing frontend dependencies (lockfile changed or first run)...${R}"
      (cd "$_fe" && npm ci)
    fi

    # Sync Python deps — clear ALL external Python env vars that interfere with uv
    UV_CLEAN_ENV="env -u VIRTUAL_ENV -u CONDA_PREFIX -u CONDA_DEFAULT_ENV -u CONDA_PYTHON_EXE"
    echo -e "${Y}Syncing Python dependencies...${R}"
    # Don't swallow errors. `tail -1` was hiding "uv sync failed" output and
    # leaving users with a half-installed venv. Show full output; if uv
    # reports an error, the user sees what actually broke.
    $UV_CLEAN_ENV uv sync || {
      echo -e "${RED}uv sync failed. Aborting startup.${R}"
      exit 1
    }
    # Ensure editable install is active. ``uv sync`` above tends to
    # strip the project's own editable install because lockfile doesn't
    # list the project as editable. ``--no-deps --reinstall`` is the
    # reliable form — ``--reinstall-package xyz-agent-context`` is
    # sometimes a no-op when uv thinks the install is already satisfied.
    $UV_CLEAN_ENV uv pip install -e "$SCRIPT_DIR" \
      --python "$SCRIPT_DIR/.venv/bin/python3" \
      --no-deps --reinstall || {
      echo -e "${RED}editable install failed. Aborting startup.${R}"
      exit 1
    }
    # Verify import works
    "$SCRIPT_DIR/.venv/bin/python3" -c "import xyz_agent_context" 2>/dev/null || {
      echo -e "${RED}xyz_agent_context still not importable. Rebuilding venv from scratch...${R}"
      rm -rf "$SCRIPT_DIR/.venv"
      $UV_CLEAN_ENV uv sync || { echo -e "${RED}uv sync failed.${R}"; exit 1; }
      $UV_CLEAN_ENV uv pip install -e "$SCRIPT_DIR" --python "$SCRIPT_DIR/.venv/bin/python3" || {
        echo -e "${RED}editable install failed after rebuild. Manual fix needed:${R}"
        echo "  rm -rf .venv && uv sync && uv pip install -e ."
        exit 1
      }
      # Final check after rebuild
      "$SCRIPT_DIR/.venv/bin/python3" -c "import xyz_agent_context" || {
        echo -e "${RED}STILL not importable. Tell maintainer.${R}"
        exit 1
      }
    }

    # Start everything
    exec "$SCRIPT_DIR/scripts/dev-local.sh"
    ;;
esac
