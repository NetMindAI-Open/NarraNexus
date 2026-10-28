#!/usr/bin/env bash
#
# @file_name: netmind_env.sh
# @date: 2026-09-09
# @description: NetMind ("Power") login environment for a source-run local
#               stack (run.sh -> scripts/dev/dev-local.sh). Sourced, not run.
#
# Why this exists
# ---------------
# `bash run.sh` is how the open-source "local version" is installed and run,
# and it execs dev-local.sh. That script used to turn Power login ON by
# default pointed at protago-DEV, and exported no VITE_NETMIND_* at all, so
# the vite dev server fell back to its compiled-in protago-dev endpoints:
# every source-run user's "Sign in with GitHub" opened "Netmind AI Test by
# protagohhz" and redirected to accounts.protago-dev.com (B-40, upstream
# NetMindAI-Open/NarraNexus#90), and their real NetMind accounts were sent to
# the dev auth service (#89).
#
# Now the NetMind environment is ONE choice that sets the backend AND the
# frontend together, so they can never disagree:
#   NEXUS_NETMIND_ENV=prod  (default) production NetMind
#   NEXUS_NETMIND_ENV=dev             the protago-dev stack (internal testing)
# Every individual variable stays overridable (an already-exported value
# wins). NEXUS_DEV_POWER_LOGIN=0 keeps a pure-local (username-only) session:
# both login flags are exported as an explicit `false` (an old
# `export VITE_ENABLE_POWER_LOGIN=true` in the developer's shell must not
# light the frontend entry against the vite dev fallback), and every other
# NetMind var is unset in the panes rather than forwarded.
#
# Exporting is not enough for dev-local.sh: tmux panes inherit the tmux
# SERVER's environment (captured when the server first started), not the
# launcher's. nexus_netmind_env_cmd prints `export VAR='value'; ` for every
# NetMind var so the launcher can prepend it to each pane's command, the
# same way it forwards PATH — vite reads VITE_-prefixed process env at dev
# time, the backend reads the rest. Values are shell-quoted with printf %q,
# so any character (a single quote included) survives the round trip.
#
# nexus_netmind_env returns non-zero on an unknown NEXUS_NETMIND_ENV; the
# caller aborts.

NEXUS_NETMIND_VARS="NARRANEXUS_ENABLE_POWER_LOGIN NETMIND_USE_SUBSCRIPTION_ENABLED NETMIND_AUTH_API_URL BILLING_API_BASE NETMIND_KEY_API_BASE NETMIND_INFERENCE_BASE VITE_ENABLE_POWER_LOGIN VITE_NETMIND_AUTH_API VITE_NETMIND_ACCOUNTS_URL VITE_NETMIND_SYS_CODE VITE_NETMIND_REGISTER_URL"

NEXUS_NETMIND_FLAG_VARS="NARRANEXUS_ENABLE_POWER_LOGIN VITE_ENABLE_POWER_LOGIN"

# The single predicate both functions below share, so "Power login off"
# can never mean different things to the exporter and the forwarder.
nexus_power_login_off() {
    [ "${NEXUS_DEV_POWER_LOGIN:-1}" = "0" ]
}

nexus_netmind_env() {
    if nexus_power_login_off; then
        export NARRANEXUS_ENABLE_POWER_LOGIN=false
        export VITE_ENABLE_POWER_LOGIN=false
        return 0
    fi

    local auth accounts register billing key inference
    case "${NEXUS_NETMIND_ENV:-prod}" in
        prod)
            auth="https://auth-api.netmind.ai"
            accounts="https://accounts.netmind.ai"
            register="https://www.netmind.ai/sign/register"
            billing="https://billing.api.netmind.ai"
            key="https://inference.api.netmind.ai"
            inference="https://api.netmind.ai/inference-api"
            ;;
        dev)
            auth="https://userauth.protago-dev.com"
            accounts="https://accounts.protago-dev.com"
            register="https://netmind-power-site.protago-dev.com/sign/register"
            billing="https://billing.api.protago-dev.com"
            key="https://inference.api.protago-dev.com"
            inference="https://test.api.netmind.ai/inference-api"
            ;;
        *)
            echo "NEXUS_NETMIND_ENV='${NEXUS_NETMIND_ENV}' is not one of: prod, dev" >&2
            return 1
            ;;
    esac

    export NARRANEXUS_ENABLE_POWER_LOGIN="${NARRANEXUS_ENABLE_POWER_LOGIN:-true}"
    export NETMIND_USE_SUBSCRIPTION_ENABLED="${NETMIND_USE_SUBSCRIPTION_ENABLED:-true}"
    export NETMIND_AUTH_API_URL="${NETMIND_AUTH_API_URL:-$auth}"
    export BILLING_API_BASE="${BILLING_API_BASE:-$billing}"
    export NETMIND_KEY_API_BASE="${NETMIND_KEY_API_BASE:-$key}"
    export NETMIND_INFERENCE_BASE="${NETMIND_INFERENCE_BASE:-$inference}"
    export VITE_ENABLE_POWER_LOGIN="${VITE_ENABLE_POWER_LOGIN:-true}"
    export VITE_NETMIND_AUTH_API="${VITE_NETMIND_AUTH_API:-$auth}"
    export VITE_NETMIND_ACCOUNTS_URL="${VITE_NETMIND_ACCOUNTS_URL:-$accounts}"
    export VITE_NETMIND_SYS_CODE="${VITE_NETMIND_SYS_CODE:-f925fc2c}"
    export VITE_NETMIND_REGISTER_URL="${VITE_NETMIND_REGISTER_URL:-$register}"
}

nexus_netmind_env_cmd() {
    local var value quoted out=""
    for var in $NEXUS_NETMIND_VARS; do
        if nexus_power_login_off; then
            case " $NEXUS_NETMIND_FLAG_VARS " in
                *" $var "*) out+="export $var=false; " ;;
                *) out+="unset $var; " ;;
            esac
            continue
        fi
        eval "value=\"\${$var-}\""
        if [ -n "$value" ]; then
            printf -v quoted '%q' "$value"
            out+="export $var=$quoted; "
        fi
    done
    printf '%s' "$out"
}
