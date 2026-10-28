"""
@file_name: test_dev_netmind_env.py
@date: 2026-09-09
@description: A source-run local stack (run.sh -> dev-local.sh) uses PROD
NetMind unless the developer asks for protago-dev.

B-40 / upstream NetMindAI-Open/NarraNexus#90: dev-local.sh enabled Power login
against protago-dev by default and exported no VITE_NETMIND_*, so the vite dev
server fell back to accounts.protago-dev.com and "Sign in with GitHub" opened
the dev OAuth app for every source-run user. These tests source the real
scripts/dev/netmind_env.sh in a clean bash.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NETMIND_ENV = REPO / "scripts" / "dev" / "netmind_env.sh"
DEV_LOCAL = REPO / "scripts" / "dev" / "dev-local.sh"
RUNTIME_CONFIG = REPO / "frontend" / "src" / "lib" / "runtimeConfig.ts"
ENV_EXAMPLE = REPO / ".env.example"


NETMIND_VARS = (
    "NARRANEXUS_ENABLE_POWER_LOGIN NETMIND_USE_SUBSCRIPTION_ENABLED "
    "NETMIND_AUTH_API_URL BILLING_API_BASE NETMIND_KEY_API_BASE "
    "NETMIND_INFERENCE_BASE VITE_ENABLE_POWER_LOGIN VITE_NETMIND_AUTH_API "
    "VITE_NETMIND_ACCOUNTS_URL VITE_NETMIND_SYS_CODE VITE_NETMIND_REGISTER_URL"
).split()


def _resolve(
    env: dict[str, str], *, pane_inherits: bool = False
) -> tuple[int, dict[str, str], str]:
    """Source netmind_env.sh with ``env``, then run the forwarded command in
    a simulated tmux pane and return the NetMind vars the pane ends up with.

    A pane's shell inherits the tmux SERVER's environment, not the
    launcher's: ``pane_inherits=False`` models a server that has none of
    the vars (so only what the command forwards reaches the pane);
    ``pane_inherits=True`` models a server started from the same shell
    (so the command must also override/unset stale values)."""
    pane_env = '"${launcher_env[@]}"' if pane_inherits else ""
    script = (
        f"source '{NETMIND_ENV}' && nexus_netmind_env || exit $?; "
        'cmd="$(nexus_netmind_env_cmd)"; '
        "launcher_env=(); "
        + "".join(
            f'[ -n "${{{k}+x}}" ] && launcher_env+=("{k}=${{{k}}}"); '
            for k in env
        )
        + f'env -i PATH="$PATH" {pane_env} bash -c "$cmd env -0"'
    )
    r = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    exported: dict[str, str] = {}
    for item in r.stdout.split("\0"):
        key, sep, value = item.partition("=")
        if sep and key in NETMIND_VARS:
            exported[key] = value
    return r.returncode, exported, r.stderr


def test_default_is_prod_for_backend_and_frontend():
    rc, env, _ = _resolve({})
    assert rc == 0
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.netmind.ai"
    assert env["VITE_NETMIND_AUTH_API"] == "https://auth-api.netmind.ai"
    assert env["NETMIND_AUTH_API_URL"] == "https://auth-api.netmind.ai"
    assert env["NETMIND_INFERENCE_BASE"] == "https://api.netmind.ai/inference-api"
    assert env["VITE_ENABLE_POWER_LOGIN"] == env["NARRANEXUS_ENABLE_POWER_LOGIN"] == "true"
    assert not any("protago-dev" in v for v in env.values())


def test_dev_opt_in_targets_protago_dev_consistently():
    rc, env, _ = _resolve({"NEXUS_NETMIND_ENV": "dev"})
    assert rc == 0
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.protago-dev.com"
    assert env["NETMIND_AUTH_API_URL"] == env["VITE_NETMIND_AUTH_API"] == (
        "https://userauth.protago-dev.com"
    )


def test_explicit_override_wins():
    rc, env, _ = _resolve({"VITE_NETMIND_ACCOUNTS_URL": "https://accounts.example.netmind.ai"})
    assert rc == 0
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.example.netmind.ai"


def test_power_login_off_forwards_explicit_false_only():
    rc, env, _ = _resolve({"NEXUS_DEV_POWER_LOGIN": "0"})
    assert rc == 0
    assert env == {
        "NARRANEXUS_ENABLE_POWER_LOGIN": "false",
        "VITE_ENABLE_POWER_LOGIN": "false",
    }


@pytest.mark.parametrize("pane_inherits", [False, True])
def test_power_login_off_overrides_a_stale_shell_export(pane_inherits):
    """PR#403 review I3: NEXUS_DEV_POWER_LOGIN=0 plus a leftover
    `export VITE_ENABLE_POWER_LOGIN=true` (and protago-dev endpoints) in
    the developer's shell must not light the frontend Power entry, which
    would fall back to the vite dev server's protago-dev endpoints."""
    stale = {
        "NEXUS_DEV_POWER_LOGIN": "0",
        "VITE_ENABLE_POWER_LOGIN": "true",
        "NARRANEXUS_ENABLE_POWER_LOGIN": "true",
        "VITE_NETMIND_ACCOUNTS_URL": "https://accounts.protago-dev.com",
        "NETMIND_AUTH_API_URL": "https://userauth.protago-dev.com",
    }
    rc, env, _ = _resolve(stale, pane_inherits=pane_inherits)
    assert rc == 0
    assert env == {
        "NARRANEXUS_ENABLE_POWER_LOGIN": "false",
        "VITE_ENABLE_POWER_LOGIN": "false",
    }


def test_values_with_a_single_quote_round_trip():
    """PR#403 review M7: the forwarded command must quote any value."""
    odd = "https://www.netmind.ai/sign/register?ref=it's; echo pwned"
    rc, env, _ = _resolve({"VITE_NETMIND_REGISTER_URL": odd})
    assert rc == 0
    assert env["VITE_NETMIND_REGISTER_URL"] == odd
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.netmind.ai"


def test_unknown_env_aborts():
    rc, _, err = _resolve({"NEXUS_NETMIND_ENV": "staging"})
    assert rc != 0
    assert "NEXUS_NETMIND_ENV" in err


def test_dev_local_forwards_netmind_env_into_every_tmux_window():
    """tmux panes inherit the tmux SERVER's env, not the launcher's: the
    resolved vars must ride in each window command, the frontend's too."""
    text = DEV_LOCAL.read_text()
    assert 'source "$SCRIPT_DIR/netmind_env.sh"' in text
    assert 'NETMIND_ENV="$(nexus_netmind_env_cmd)"' in text
    env_cmd = next(ln for ln in text.splitlines() if ln.startswith('ENV_CMD="'))
    assert "${NETMIND_ENV}" in env_cmd
    frontend = next(ln for ln in text.splitlines() if "Frontend Dev Server" in ln)
    assert "${NETMIND_ENV}" in frontend
    assert "protago-dev.com" not in text  # endpoints live in netmind_env.sh only


def _prod_branch_literals() -> dict[str, str]:
    text = NETMIND_ENV.read_text()
    prod = re.search(r"^\s*prod\)\n(.*?);;", text, re.S | re.M)
    assert prod, "prod) branch not found in netmind_env.sh"
    return dict(re.findall(r'(\w+)="([^"]+)"', prod.group(1)))


def test_prod_endpoints_agree_across_copies():
    """PR#403 review M6: the prod NetMind endpoints live in three places
    (bash, the TS build fallback, .env.example); a host migration that
    edits one copy must turn this red."""
    sh = _prod_branch_literals()
    ts = RUNTIME_CONFIG.read_text()
    block = re.search(r"const _PROD_NETMIND[^=]*=\s*\{(.*?)\};", ts, re.S)
    assert block, "_PROD_NETMIND not found in runtimeConfig.ts"
    ts_vals = dict(re.findall(r"(\w+):\s*'([^']+)'", block.group(1)))
    assert ts_vals["authApi"] == sh["auth"]
    assert ts_vals["accountsUrl"] == sh["accounts"]
    assert ts_vals["registerUrl"] == sh["register"]
    assert ts_vals["sysCode"] in NETMIND_ENV.read_text()

    example = ENV_EXAMPLE.read_text()
    for var, key in (
        ("NETMIND_AUTH_API_URL", "auth"),
        ("BILLING_API_BASE", "billing"),
        ("NETMIND_KEY_API_BASE", "key"),
        ("NETMIND_INFERENCE_BASE", "inference"),
    ):
        m = re.search(rf"^#\s*{var}=(\S+)", example, re.M)
        assert m, f"{var} example missing from .env.example"
        assert m.group(1) == sh[key], var
