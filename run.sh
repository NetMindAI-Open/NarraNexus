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
    "7801:MCP Modules (every module server mounted by path)" "47831:Channel health (aggregated, every IM channel)")
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
  for port in 8100 8000 5173 5174 7801 47831; do
    lsof -ti:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
  done
  # Kill known process patterns
  pkill -f "sqlite_proxy_server" 2>/dev/null || true
  pkill -f "uvicorn backend.main:app" 2>/dev/null || true
  pkill -f "narranexus.platform.module_system.module_runner mcp" 2>/dev/null || true
  pkill -f "run_worker_supervisor" 2>/dev/null || true
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

  # Run a command in the background with a hard timeout. `timeout(1)` isn't on
  # stock macOS, so this uses only shell primitives. Returns the command's exit
  # code, or 124 if it was killed for exceeding <timeout>s. Single source of
  # truth for every install/preflight step below (was copy-pasted 4×).
  # Usage: _run_with_timeout <timeout_s> <label> <cmd> [args...]
  _run_with_timeout() {
    local _to="$1"; local _label="$2"; shift 2
    "$@" &
    local _pid=$!
    local _el=0
    while kill -0 "$_pid" 2>/dev/null; do
      if [ "$_el" -ge "$_to" ]; then
        echo -e "${RED}${_label} hung > ${_to}s — killing.${R}"
        kill -9 "$_pid" 2>/dev/null
        wait "$_pid" 2>/dev/null
        return 124
      fi
      sleep 1
      _el=$((_el + 1))
    done
    wait "$_pid"
    return $?
  }

  _try_install_lark_cli() {
    local action="$1"  # "Installing" / "Updating" (bash 3.2 lacks ${var^})
    echo -e "${Y}${action} lark-cli (timeout ${_LARK_CLI_TIMEOUT}s)...${R}"
    _run_with_timeout "$_LARK_CLI_TIMEOUT" "npm install lark-cli" \
      npm install -g @larksuite/cli
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

  # Claude Code is NO LONGER installed here. On the lightweight build it is an
  # OPTIONAL plugin the user installs on demand from Settings → Plugins, which
  # runs an `npm install --prefix ~/.narranexus/plugins/nodejs
  # @anthropic-ai/claude-code@<pinned>` plus a `python -m pip install --target
  # ~/.narranexus/plugins/pyenv/claude_code claude-agent-sdk`. The pinned version
  # and the install itself live in backend/integrations/plugins/ (registry.py
  # imports PINNED_CLI_VERSION from cli_binary.py — still the single source of
  # truth).
  # An un-installed framework is fail-closed at get_agent_loop_driver, never a
  # silent fallback; nexus_power (built-in) always works. Node stays required
  # below for lark-cli / narra-cli.

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
    # bash -c preserves the pipe-to-tail (helper runs a single command).
    _run_with_timeout "$_LARK_SKILLS_TIMEOUT" "npx skills add" \
      bash -c 'HOME="$HOME" npx skills add larksuite/cli -y -g 2>&1 | tail -3'
  }

  if ! ls ~/.agents/skills/lark-shared/SKILL.md &>/dev/null 2>&1 \
     && ! ls ~/.claude/skills/lark-shared/SKILL.md &>/dev/null 2>&1; then
    if ! _try_install_lark_skills; then
      echo -e "${Y}⚠ Lark skill install failed/timed out — `lark_skill(...)` MCP tool will return 'not found'. Lark/Feishu features degrade to runtime help (`<domain> +<cmd> --help`).${R}"
      echo "  Retry later: HOME=\$HOME npx skills add larksuite/cli -y -g"
      echo ""
    fi
  fi

  # Install narra-cli (@narra-im/narra-cli) — the NarraMessenger MCP tool
  # `narra_cli` spawns it (via narra_cli_client.py) for outbound
  # query/context/speech/explore. Installed LOCALLY here (a per-prefix install,
  # not -g): the upstream runtime guide rejects global installs for local dev.
  # (In a controlled BUILD image `-g` is fine and is what the deploy images do —
  # see the cloud-parity note below.) We pin the install prefix to
  # $NARRA_CLI_HOME and export NARRA_CLI_BIN so narra_cli_client.py resolves it
  # deterministically.
  # VERSION IS PINNED (not track-latest): narra-cli is a thin client to narra's
  # OWN evolving hosted backend (unlike lark-cli → stable public OpenAPI), so it
  # is claude-code-like — pin + deliberate bump, for reproducibility (every user
  # gets the same validated binary) and no client-ahead-of-backend skew. The CLI
  # barely moves (npm has ~4 releases); bump _NARRA_CLI_VERSION here + Dockerfile
  # + the DMG bundle + the cloud image together when narra ships a new CLI you
  # have validated against the hosted backend. Graceful degrade: if install
  # fails, NarraMessenger receive still works (Matrix /sync); only CLI-backed
  # query degrades.
  # 1.2.x CONTRACT: every API command REQUIRES --endpoint (the `configure`
  # command + global config file are gone); narra_cli_client injects it from
  # each agent's binding. 1.2.x declares engines.node>=22 but runs on node 20
  # (engine warning only — verified 2026-09-09); do not pin below 1.2.0 without
  # reverting the injection.
  #
  # NOTE — cloud parity: `narra_cli` runs in the MCP container, so the cloud
  # image that needs narra-cli is the MCP/backend image
  # (NarraNexus-deploy/docker/Dockerfile.python), NOT the executor image (the
  # executor never shells narra-cli, same as lark-cli). Installed there in
  # deploy commit 4007ca5 (dev) / 4b32444 (main). Missing it there ships cloud
  # NarraMessenger `narra_cli` dead (same class as the officecli v1.9.0 miss).
  # Install under ~/.narranexus (NOT the repo tree): narra_cli_client's
  # resolver lists this dir in _discover_node_bin_dirs, so the MCP process
  # finds it in BOTH run modes — `bash run.sh` (env-exported) and the
  # 4-terminal `make dev-mcp` path (which never sees run.sh's export).
  _NARRA_CLI_HOME="${NARRA_CLI_HOME:-$HOME/.narranexus/narra-cli}"
  _NARRA_CLI_VERSION="1.2.1"
  _NARRA_CLI_TIMEOUT=120
  export NARRA_CLI_BIN="$_NARRA_CLI_HOME/node_modules/.bin/narra-cli"

  _try_install_narra_cli() {
    local action="$1"  # "Installing" / "Updating" (bash 3.2 lacks ${var^})
    echo -e "${Y}${action} narra-cli@${_NARRA_CLI_VERSION} (timeout ${_NARRA_CLI_TIMEOUT}s)...${R}"
    mkdir -p "$_NARRA_CLI_HOME"
    _run_with_timeout "$_NARRA_CLI_TIMEOUT" "npm install narra-cli" \
      npm install --prefix "$_NARRA_CLI_HOME" "@narra-im/narra-cli@${_NARRA_CLI_VERSION}"
  }

  # Install when missing OR when the installed version != the pin (so a bump of
  # _NARRA_CLI_VERSION takes effect on the next start — same pattern as officecli).
  _narra_installed_ver=""
  [ -x "$NARRA_CLI_BIN" ] && _narra_installed_ver=$("$NARRA_CLI_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  if [ "$_narra_installed_ver" != "$_NARRA_CLI_VERSION" ]; then
    if ! _try_install_narra_cli "Installing"; then
      echo -e "${Y}⚠ narra-cli not available — NarraMessenger CLI send/query features will be disabled (receive via Matrix /sync still works).${R}"
      echo "  Retry: npm install --prefix \"$_NARRA_CLI_HOME\" @narra-im/narra-cli@${_NARRA_CLI_VERSION}"
      echo ""
    fi
  fi

  # No endpoint config step: since narra-cli 1.2 there is no `configure` and no
  # global config — the endpoint is a per-call flag that narra_cli_client
  # injects from each agent's binding (backend_base_url), so one process serves
  # api.netmind.chat / api-test / api-cn bindings side by side.
  # NARRA_BACKEND_ENDPOINT is now ONLY the doctor preflight's probe target.
  #
  # Compat preflight: `doctor` checks the CLI install + endpoint reachability.
  # It makes a NETWORK call, so it MUST run under the timeout guard (a DNS
  # blackhole / dead endpoint would otherwise hang startup forever — the very
  # "never wedge startup" the comment promised). 15s cap, non-fatal: a timeout
  # (124) or any non-zero just surfaces a WARNING.
  _NARRA_DOCTOR_ENDPOINT="${NARRA_BACKEND_ENDPOINT:-https://api.netmind.chat}"
  if [ -x "$NARRA_CLI_BIN" ]; then
    if _run_with_timeout 15 "narra-cli doctor" "$NARRA_CLI_BIN" doctor --endpoint "$_NARRA_DOCTOR_ENDPOINT" >/dev/null 2>&1; then
      echo -e "${Y}narra-cli doctor OK (v${_NARRA_CLI_VERSION}).${R}"
    else
      echo -e "${Y}⚠ narra-cli doctor failed/timed out (CLI/endpoint compat?) — NarraMessenger CLI ops may fail. Run: ${NARRA_CLI_BIN} doctor --endpoint ${_NARRA_DOCTOR_ENDPOINT}${R}"
    fi
  fi

  # Install OfficeCLI (optional — powers the built-in `officecli` skill for
  # docx/xlsx/pptx). GitHub-Releases self-contained binary (embedded .NET, no
  # deps), so we follow the uv pattern: curl the right per-OS/arch asset to
  # ~/.local/bin (already on PATH via the uv export above). Graceful-degrade:
  # if it fails the platform still works, only Office-document skills are off.
  # Version pinned. officecli ships from FOUR independent places and they must
  # agree — bump all of them together:
  #   run.sh (here)                      local run
  #   scripts/release/build-desktop.sh   the macOS app bundle
  #   deploy: docker/Dockerfile.python   cloud backend + workers
  #   deploy: docker/Dockerfile.executor cloud agent  <- the one that matters
  #                                      for the builtin skill; missed once and
  #                                      the cloud feature shipped dead (v1.9.0)
  # The last two live in the NarraNexus-deploy repo, which gates the four pins
  # in scripts/check_executor_clis.sh (a prerequisite of `make app-build`).
  # (This list previously named docker/Dockerfile.manyfold, which does not
  # exist, and omitted both cloud images entirely.)
  _OFFICECLI_VERSION="v1.0.135"

  _try_install_officecli() {
    local asset
    case "$(uname -s)_$(uname -m)" in
      Darwin_arm64)          asset=officecli-mac-arm64 ;;
      Darwin_x86_64)         asset=officecli-mac-x64 ;;
      Linux_x86_64|Linux_amd64) asset=officecli-linux-x64 ;;
      Linux_aarch64|Linux_arm64) asset=officecli-linux-arm64 ;;
      *) echo -e "${Y}⚠ officecli: unsupported platform $(uname -s)/$(uname -m) — skipping.${R}"; return 1 ;;
    esac
    local url="https://github.com/iOfficeAI/OfficeCLI/releases/download/${_OFFICECLI_VERSION}/${asset}"
    echo -e "${Y}Installing officecli ${_OFFICECLI_VERSION} (${asset})...${R}"
    mkdir -p "$HOME/.local/bin"
    if curl -fsSL -o "$HOME/.local/bin/officecli" "$url"; then
      chmod +x "$HOME/.local/bin/officecli"
      return 0
    fi
    return 1
  }

  if ! command -v officecli &>/dev/null; then
    if ! _try_install_officecli; then
      echo -e "${Y}⚠ officecli not available — Office-document (docx/xlsx/pptx) skill disabled.${R}"
      echo "  Retry later: see https://github.com/iOfficeAI/OfficeCLI/releases"
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
  # The telemetry consent marker must live on the PERSISTED surface:
  # HOME (/home/app) is container writable layer — a rebuild/upgrade
  # wipes it, silently reverting a full-level sandbox from "user opted
  # out" back to shipping. /data is the volume every container-mode
  # deployment mounts (workspaces/logs/db already live there), so the
  # user's opt-out survives exactly as long as their data does.
  export NEXUS_DIAG_OPTOUT_FILE="${NEXUS_DIAG_OPTOUT_FILE:-/data/telemetry_optout}"
  export DATABASE_URL="${DATABASE_URL:-sqlite:////data/nexus.db}"
  export DASHBOARD_BIND_HOST="${DASHBOARD_BIND_HOST:-0.0.0.0}"
  # This container launcher serves the hosted/server form factor. The cloud
  # stack may start uvicorn directly, where deployment-mode inference supplies
  # the same label; an explicit override still wins here.
  export NARRA_SURFACE="${NARRA_SURFACE:-cloud}"
  # Telemetry env label (routing + envelope). Deployment-layer detection
  # lives HERE on purpose: the logging utility (_ship.py) must not sniff
  # another integration's env vars. Staging manyfold sandboxes identify
  # themselves by the webhook host they were provisioned with.
  # Manyfold-managed sandbox detection mirrors the canonical identity
  # resolver (integrations/manyfold_outbound.manyfold_runtime_env):
  # token + runtime_id, BOTH required — the webhook URL is explicitly
  # allowed to be absent (channel-send escape hatch), so gating on it
  # would silently skip real sandboxes AND fire on self-hosts that
  # merely set the URL.
  _is_manyfold_sandbox=""
  if [ -n "${MANYFOLD_SYNC_WEBHOOK_TOKEN:-}" ] && [ -n "${MANYFOLD_RUNTIME_ID:-}" ]; then
    _is_manyfold_sandbox="1"
  fi
  if [ -z "${NEXUS_DIAG_ENV:-}" ] && [ -n "$_is_manyfold_sandbox" ]; then
    # Every managed sandbox gets its own label: "staging" routes to the
    # dev collector; everything else is "sprite" — its own storage
    # partition, so a full-level sandbox fleet rotates ITSELF under the
    # collector's size cap instead of crowding out prod's history.
    # Label contract (four-sided): "sprite" is in the collector's
    # default DIAG_COLLECT_KNOWN_ENVS and the discovery-map examples;
    # its discovery endpoint is the built-in prod default (only
    # "staging" is redirected to dev, in the block below).
    case "${MANYFOLD_SYNC_WEBHOOK_URL:-}" in
      *api-staging*) export NEXUS_DIAG_ENV="staging" ;;
      *)             export NEXUS_DIAG_ENV="sprite" ;;
    esac
  fi
  # Staging sandboxes discover their ingest URL from the DEV collector,
  # not the built-in prod default: staging telemetry is fully self-
  # contained on dev-agent (its /v1/config already serves a staging ->
  # dev-ingest map), so staging validation never waits on the prod
  # collector being deployed. Only staging is redirected; sprite (prod
  # manyfold) keeps the built-in prod discovery so it routes to the
  # prod collector. Explicit env still wins.
  #
  # KNOWN RESIDUAL (accepted 2026-08-12): the staging/sprite split is a
  # substring sniff of the manyfold webhook host (api-staging). A prod
  # sandbox whose host ever contained "api-staging" would mislabel and
  # ship prod logs to the dev collector; conversely a staging host that
  # dropped the substring would fall through to prod discovery and drop
  # batches. Both collapse to a config fix once observed — staging
  # validation only needs the common case, which this covers. Hardening
  # (manyfold injecting NEXUS_DIAG_ENV explicitly) is deferred. When it
  # lands, the injector must set token + runtime_id too: the redirect
  # below is gated on label AND manyfold identity, so an injected
  # "staging" label with those vars absent gets the label but not the
  # redirect, and its batches fall back to prod discovery silently.
  # Gated on _is_manyfold_sandbox too: the "staging" label is only
  # auto-derived for sandboxes, but a personal install that set
  # NEXUS_DIAG_ENV=staging by hand must NOT get its logs redirected to
  # our dev collector — the redirect is a managed-sandbox behavior, not
  # a label anyone can opt into.
  if [ -n "$_is_manyfold_sandbox" ] \
     && [ "${NEXUS_DIAG_ENV:-}" = "staging" ] \
     && [ -z "${NEXUS_DIAG_DISCOVERY_URL:-}" ]; then
    export NEXUS_DIAG_DISCOVERY_URL="https://dev-agent.narra.nexus/telemetry/v1/config"
  fi
  # Managed sandboxes DEFAULT to full — via the consent chain's
  # managed-DEFAULT layer, not the env override: full logs are the
  # point of sandbox telemetry (joint support debugging), but the
  # user's opt-out marker still wins and the settings toggle stays
  # live. An NEXUS_DIAG_SHIP override here would grey the toggle and
  # 409 the PUT — confiscating the switch on exactly the single-tenant
  # user runtime the consent design was built for.
  if [ -n "$_is_manyfold_sandbox" ] && [ -z "${NEXUS_DIAG_DEFAULT_SHIP:-}" ]; then
    export NEXUS_DIAG_DEFAULT_SHIP="full"
  fi

  mkdir -p "$BASE_WORKING_PATH" "$NEXUS_LOG_DIR" /data
  mkdir -p "$(dirname /data/nexus.db)" || true

  # 1. sqlite_proxy (only when DATABASE_URL is sqlite-ish)
  if [[ "${DATABASE_URL}" == sqlite* ]]; then
    "$SCRIPT_DIR/.venv/bin/python3" -m narranexus.platform.utils.db.sqlite_proxy_server &
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
    # (the proxy was started but nobody talks to it). scripts/dev/dev-local.sh
    # exports the same var for the local-development path.
    export SQLITE_PROXY_URL="${SQLITE_PROXY_URL:-http://127.0.0.1:8100}"
  fi

  # 2. MCP module runner (stays its own process — ONE port, every module server mounted by path)
  "$SCRIPT_DIR/.venv/bin/python3" -m narranexus.platform.module_system.module_runner mcp &
  # 3. Worker supervisor — ONE process running poller / job / message-bus / all
  #     IM channel triggers in a single event loop, each as a supervised task
  #     with backoff-restart, sharing one package import + one DB pool. Replaces
  #     the old four-process layout (module_poller, job_trigger,
  #     message_bus_trigger, run_channel_triggers). message_bus_trigger
  #     deliberately defers IM channels to the channels worker, so without this
  #     supervisor inbound IM messages are never received (issue #54).
  #
  #     NEXUS_EXTERNAL_TRIGGERS=1 hands the clock and the ears to the hosting
  #     platform (Manyfold): the sandbox suspends while idle, so an in-process
  #     scheduler silently misses alarms, and a second consumer of the same bot
  #     fights the platform's (Telegram getUpdates vs webhook 409, duplicate
  #     Discord gateway deliveries). Drop exactly those two workers — 'poller'
  #     and 'bus' are sandbox-local and keep running.
  supervisor_args=()
  if [ "${NEXUS_EXTERNAL_TRIGGERS:-}" = "1" ]; then
    supervisor_args+=(--exclude jobs,channels)
    echo "NEXUS_EXTERNAL_TRIGGERS=1 — worker supervisor excludes jobs,channels (platform-managed)"
  fi
  "$SCRIPT_DIR/.venv/bin/python3" -m narranexus.platform.module_system.run_worker_supervisor \
    "${supervisor_args[@]+"${supervisor_args[@]}"}" &

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
    "$SCRIPT_DIR/.venv/bin/python3" -c "import narranexus.kernel" 2>/dev/null || {
      echo -e "${RED}narranexus still not importable. Rebuilding venv from scratch...${R}"
      rm -rf "$SCRIPT_DIR/.venv"
      $UV_CLEAN_ENV uv sync || { echo -e "${RED}uv sync failed.${R}"; exit 1; }
      # `--no-deps`, like the non-fallback path above: without it this
      # re-resolves every dependency from the pyproject ranges instead of
      # leaving the graph `uv sync` just installed from the lock. Harmless in
      # practice (the lock's versions all satisfy the floors, so uv changes
      # nothing), but it is the same shape as the 2026-09-10 dmg bug and there
      # is no reason for the two branches to differ.
      $UV_CLEAN_ENV uv pip install -e "$SCRIPT_DIR" --no-deps --reinstall \
        --python "$SCRIPT_DIR/.venv/bin/python3" || {
        echo -e "${RED}editable install failed after rebuild. Manual fix needed:${R}"
        echo "  rm -rf .venv && uv sync && uv pip install -e ."
        exit 1
      }
      # Final check after rebuild
      "$SCRIPT_DIR/.venv/bin/python3" -c "import narranexus.kernel" || {
        echo -e "${RED}STILL not importable. Tell maintainer.${R}"
        exit 1
      }
    }

    # Start everything
    exec "$SCRIPT_DIR/scripts/dev/dev-local.sh"
    ;;
esac
