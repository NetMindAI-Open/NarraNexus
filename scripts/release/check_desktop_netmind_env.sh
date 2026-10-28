#!/usr/bin/env bash
#
# @file_name: check_desktop_netmind_env.sh
# @date: 2026-09-09
# @description: Release gate for the NetMind ("Power") login endpoints a
#               desktop (DMG) build bakes in. Fails loudly instead of letting
#               a build ship the protago-dev OAuth app.
#
# Why this exists
# ---------------
# The DMG bakes its NetMind endpoints at build time: the frontend through
# vite (VITE_* env) and the launcher through cargo option_env! (backend
# vars), both read from the shell that runs build-desktop.sh. Any value left
# empty used to fall through to a compiled-in default, and those defaults
# pointed at protago-dev — a missing repo Variable would have shipped a
# "Sign in with GitHub" that opens "Netmind AI Test" at
# accounts.protago-dev.com (B-40) with a green build.
#
# Usage
# -----
#   check_desktop_netmind_env.sh env            validate the build env
#   check_desktop_netmind_env.sh bundle <dist>  scan a built frontend dist
#
# `env` rules:
#   - VITE_ENABLE_POWER_LOGIN and NARRANEXUS_ENABLE_POWER_LOGIN must agree
#     (a frontend Power entry the backend 404s, or the reverse, is broken).
#   - NARRANEXUS_REQUIRE_POWER_LOGIN truthy (the workflow sets it on tag
#     builds) makes Power login mandatory: a release must not silently lose
#     it because the repo Variables vanished.
#   - With Power login on, every endpoint below must be set, https, on a
#     *.netmind.ai host, and not a test/dev/staging host; the frontend and
#     backend auth API must be the same host.
#   - With Power login off (a community build), nothing else is required.
# `bundle` fails if any built file still references protago-dev.
#
# Plain bash 3.2 (the macOS runner's /bin/bash): no arrays of pairs, no ${v,,}.
set -euo pipefail

REQUIRED_WHEN_POWER="VITE_NETMIND_AUTH_API VITE_NETMIND_ACCOUNTS_URL VITE_NETMIND_SYS_CODE VITE_NETMIND_REGISTER_URL NETMIND_AUTH_API_URL BILLING_API_BASE NETMIND_KEY_API_BASE NETMIND_INFERENCE_BASE"
# Endpoint vars (everything above except the sys code, which is not a URL).
URL_VARS="VITE_NETMIND_AUTH_API VITE_NETMIND_ACCOUNTS_URL VITE_NETMIND_REGISTER_URL NETMIND_AUTH_API_URL BILLING_API_BASE NETMIND_KEY_API_BASE NETMIND_INFERENCE_BASE"

errors=0
err() {
    echo "::error::check_desktop_netmind_env: $*" >&2
    errors=$((errors + 1))
}

truthy() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
        1|true|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# Print the lowercased host of an https URL, or nothing if it is not one.
https_host() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
        | sed -n 's#^https://\([^/:?#]*\).*$#\1#p'
}

check_url() {
    local name="$1" value="$2" host
    host="$(https_host "$value")"
    if [ -z "$host" ]; then
        err "$name='$value' is not an https URL"
        return
    fi
    case "$host" in
        netmind.ai|*.netmind.ai) ;;
        *) err "$name='$value' is not a *.netmind.ai host (protago-dev and other non-prod NetMind stacks are refused)"; return ;;
    esac
    # Label-exact match: test.api.netmind.ai is the dev inference stack.
    case ".$host." in
        *.test.*|*.dev.*|*.staging.*) err "$name='$value' is a non-prod NetMind host" ;;
    esac
}

check_env() {
    local fe=0 be=0 name value
    truthy "${VITE_ENABLE_POWER_LOGIN:-}" && fe=1
    truthy "${NARRANEXUS_ENABLE_POWER_LOGIN:-}" && be=1
    if [ "$fe" != "$be" ]; then
        err "VITE_ENABLE_POWER_LOGIN='${VITE_ENABLE_POWER_LOGIN:-}' and NARRANEXUS_ENABLE_POWER_LOGIN='${NARRANEXUS_ENABLE_POWER_LOGIN:-}' disagree; set both or neither"
    fi
    if [ "$fe" = 0 ] && [ "$be" = 0 ]; then
        if truthy "${NARRANEXUS_REQUIRE_POWER_LOGIN:-}"; then
            err "NARRANEXUS_REQUIRE_POWER_LOGIN is set but Power login is off — are the repo Variables (VITE_ENABLE_POWER_LOGIN / NARRANEXUS_ENABLE_POWER_LOGIN) missing?"
        else
            echo "check_desktop_netmind_env: Power login OFF (community build) — no NetMind endpoints baked"
        fi
    fi
    if [ "$fe" = 1 ] || [ "$be" = 1 ]; then
        for name in $REQUIRED_WHEN_POWER; do
            eval "value=\"\${$name:-}\""
            if [ -z "$value" ]; then
                err "$name is empty but Power login is on — it would fall back to a compiled-in default"
            fi
        done
        for name in $URL_VARS; do
            eval "value=\"\${$name:-}\""
            [ -n "$value" ] && check_url "$name" "$value"
        done
        if [ -n "${VITE_NETMIND_AUTH_API:-}" ] && [ -n "${NETMIND_AUTH_API_URL:-}" ] \
           && [ "$(https_host "$VITE_NETMIND_AUTH_API")" != "$(https_host "$NETMIND_AUTH_API_URL")" ]; then
            err "VITE_NETMIND_AUTH_API ($VITE_NETMIND_AUTH_API) and NETMIND_AUTH_API_URL ($NETMIND_AUTH_API_URL) point at different auth services"
        fi
    fi
    if [ "$errors" -gt 0 ]; then
        echo "check_desktop_netmind_env: $errors problem(s); refusing to build" >&2
        exit 1
    fi
    if [ "$fe" = 1 ]; then
        echo "check_desktop_netmind_env: Power login ON — accounts=${VITE_NETMIND_ACCOUNTS_URL} auth=${NETMIND_AUTH_API_URL}"
    fi
}

check_bundle() {
    local dist="${1:-}" hits
    if [ -z "$dist" ] || [ ! -d "$dist" ]; then
        err "bundle: dist directory '$dist' not found"
        exit 1
    fi
    hits="$(LC_ALL=C grep -rlF 'protago-dev' "$dist" || true)"
    if [ -n "$hits" ]; then
        err "built frontend still references protago-dev:"
        printf '%s\n' "$hits" >&2
        exit 1
    fi
    echo "check_desktop_netmind_env: $dist has no protago-dev reference"
}

case "${1:-}" in
    env) check_env ;;
    bundle) check_bundle "${2:-}" ;;
    *) echo "usage: $0 env | bundle <dist-dir>" >&2; exit 2 ;;
esac
