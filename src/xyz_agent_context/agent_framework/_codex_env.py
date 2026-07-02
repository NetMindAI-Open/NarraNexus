"""
@file_name: _codex_env.py
@author:
@date: 2026-06-17
@description: Build the minimal, secret-free environment for the codex
subprocess (shared by xyz_codex_official_sdk v2 and xyz_codex_cli_sdk v1).

Why this exists
---------------
Both codex spawn paths used to do ``env = {**os.environ}`` — handing the
codex subprocess the backend container's ENTIRE environment. The backend
env holds every platform secret (DB_PASSWORD, JWT_SECRET, ADMIN_SECRET_KEY,
*_API_KEY, *_SECRET, ...), so any agent that ran ``env`` / ``printenv`` /
read ``/proc/self/environ`` inside its workspace could exfiltrate all of
them. (Incident 2026-06-17.) A filesystem sandbox does NOT close this:
``env`` reads the process's own memory, not the filesystem.

The fix is to invert the default: instead of "inherit everything, blank a
few", we pass an explicit ALLOWLIST of the handful of variables codex
genuinely needs to launch and reach the LLM, and nothing else. New
secrets added to the backend ``.env`` are therefore safe by default —
they simply never reach the subprocess unless added here on purpose.

The agent's OWN (scoped, per-user, rotatable) LLM credential is the one
secret that must reach codex for it to authenticate — it flows in
explicitly via ``cli_env`` (``CodexConfig.to_cli_env`` → ``CODEX_API_KEY``),
never via ``os.environ`` passthrough.
"""
from __future__ import annotations

import os
from pathlib import Path

# Exact variable names that are safe and needed for codex (and the shell
# tools it runs) to behave. None of these carry platform secrets.
_ALLOWLIST_EXACT: frozenset[str] = frozenset({
    # Process basics — codex binary discovery + shell tool execution.
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    # Terminal / timezone.
    "TERM", "TZ",
    # Locale (LANG + the common categories; the rest via the LC_ prefix).
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
    # Temp dirs.
    "TMPDIR", "TMP", "TEMP",
    # TLS trust material — outbound HTTPS to the LLM may need a custom
    # CA bundle / cert path in some deployments.
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
})

# Outbound HTTP(S) proxy settings — required when the LLM endpoint is
# only reachable via a proxy. NO_PROXY is set explicitly below (MCP is
# local), so it is intentionally NOT inherited here. Both upper- and
# lower-case spellings are honoured by libcurl / requests / node.
_ALLOWLIST_PROXY: frozenset[str] = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
})

# Prefix allowlist — every locale category (LC_TIME, LC_NUMERIC, ...).
_ALLOWLIST_PREFIX: tuple[str, ...] = ("LC_",)

# MCP servers run on localhost; the codex subprocess must never route
# that traffic through a configured proxy.
_NO_PROXY_HOSTS = "localhost,127.0.0.1"


def build_codex_subprocess_env(
    cli_env: dict[str, str],
    codex_home: str | Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assemble the codex subprocess environment from an allowlist.

    Layering (later wins):
      1. Allowlisted variables copied from the parent ``os.environ``
         (system basics + proxy + TLS + locale). NO secrets.
      2. ``CODEX_HOME`` (per-run config/auth/state dir) + ``NO_PROXY``.
      3. ``cli_env`` — the scoped ``CODEX_API_KEY`` from
         ``CodexConfig.to_cli_env`` (the agent's own LLM credential).
      4. ``extra_env`` — per-call overrides, authoritative.

    Args:
        cli_env: Scoped auth/provider env from ``CodexConfig.to_cli_env``.
        codex_home: Per-run ``$CODEX_HOME`` directory.
        extra_env: Optional per-call overrides.

    Returns:
        A fresh dict (never mutates ``os.environ``).
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if (
            key in _ALLOWLIST_EXACT
            or key in _ALLOWLIST_PROXY
            or key.startswith(_ALLOWLIST_PREFIX)
        ):
            env[key] = value

    env["CODEX_HOME"] = str(codex_home)
    env["NO_PROXY"] = _NO_PROXY_HOSTS
    env["no_proxy"] = _NO_PROXY_HOSTS

    env.update(cli_env)
    if extra_env:
        env.update(extra_env)

    return env
