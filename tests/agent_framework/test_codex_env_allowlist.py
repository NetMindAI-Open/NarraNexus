"""
@file_name: test_codex_env_allowlist.py
@date: 2026-06-17
@description: Security regression — the codex subprocess env must be an
allowlist, NOT a copy of the backend's os.environ.

Incident 2026-06-17: an agent ran ``env`` inside its codex/claude
workspace and dumped every backend secret (DB_PASSWORD, JWT_SECRET,
ADMIN_SECRET_KEY, *_API_KEY) because the spawn code did
``env = {**os.environ}``. `build_codex_subprocess_env` replaces that
with a minimal system allowlist + CODEX_HOME + NO_PROXY + the scoped
CODEX_API_KEY. These tests lock the invariant: NO platform secret may
reach the subprocess environment.
"""
from __future__ import annotations

from pathlib import Path

from xyz_agent_context.agent_framework._codex_env import (
    build_codex_subprocess_env,
)

# Secrets that were leaking via `env` in the incident — none of these
# may ever appear in the subprocess env unless explicitly passed via
# cli_env / extra_env.
_LEAKED_SECRETS = (
    "DB_PASSWORD",
    "JWT_SECRET",
    "ADMIN_SECRET_KEY",
    "INTERNAL_INVITE_SECRET",
    "TRANSCRIPTION_HMAC_SECRET",
    "SYSTEM_DEFAULT_LLM_API_KEY",
    "SYSTEM_DEFAULT_NETMIND_API_KEY",
    "BRAVE_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)


def _seed_environ(monkeypatch):
    """Put realistic secrets + essentials into os.environ."""
    for k in _LEAKED_SECRETS:
        monkeypatch.setenv(k, f"secret-value-of-{k}")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/app")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_TIME", "en_US.UTF-8")


def test_no_platform_secret_leaks_into_env(monkeypatch):
    _seed_environ(monkeypatch)
    env = build_codex_subprocess_env(
        cli_env={"CODEX_API_KEY": "scoped-key"},
        codex_home="/tmp/codex_home",
    )
    for secret in _LEAKED_SECRETS:
        assert secret not in env, (
            f"{secret} leaked into the codex subprocess env — the "
            f"allowlist must not pass platform secrets through."
        )


def test_essentials_are_passed_through(monkeypatch):
    _seed_environ(monkeypatch)
    env = build_codex_subprocess_env(
        cli_env={}, codex_home="/tmp/codex_home"
    )
    # codex needs PATH to find its binary + shell tools; HOME + locale
    # keep tools from misbehaving.
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert env["HOME"] == "/home/app"
    assert env["LANG"] == "C.UTF-8"
    # LC_* locale categories pass through by prefix.
    assert env["LC_TIME"] == "en_US.UTF-8"


def test_proxy_and_tls_vars_pass_through_when_present(monkeypatch):
    _seed_environ(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    monkeypatch.setenv("https_proxy", "http://proxy:8080")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca.pem")
    env = build_codex_subprocess_env(
        cli_env={}, codex_home="/tmp/codex_home"
    )
    # Outbound LLM calls may need a proxy / custom CA bundle.
    assert env["HTTPS_PROXY"] == "http://proxy:8080"
    assert env["https_proxy"] == "http://proxy:8080"
    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/ca.pem"


def test_codex_home_and_no_proxy_are_set(monkeypatch):
    _seed_environ(monkeypatch)
    env = build_codex_subprocess_env(
        cli_env={}, codex_home=Path("/tmp/agent_home")
    )
    assert env["CODEX_HOME"] == "/tmp/agent_home"
    # MCP servers are local — never route them through a proxy.
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["no_proxy"] == "localhost,127.0.0.1"


def test_scoped_cli_env_is_applied(monkeypatch):
    _seed_environ(monkeypatch)
    env = build_codex_subprocess_env(
        cli_env={"CODEX_API_KEY": "scoped-llm-key"},
        codex_home="/tmp/codex_home",
    )
    # The agent's own (scoped) LLM credential is the ONE secret allowed
    # in — it flows explicitly via cli_env, not via os.environ.
    assert env["CODEX_API_KEY"] == "scoped-llm-key"


def test_inherited_codex_api_key_does_not_leak(monkeypatch):
    """A stray CODEX_API_KEY in the parent env must NOT pass through —
    only the scoped one from cli_env is authoritative (cross-tenant
    leak guard)."""
    _seed_environ(monkeypatch)
    monkeypatch.setenv("CODEX_API_KEY", "OTHER-TENANT-KEY")
    env = build_codex_subprocess_env(
        cli_env={"CODEX_API_KEY": "my-scoped-key"},
        codex_home="/tmp/codex_home",
    )
    assert env["CODEX_API_KEY"] == "my-scoped-key"


def test_extra_env_overrides(monkeypatch):
    _seed_environ(monkeypatch)
    env = build_codex_subprocess_env(
        cli_env={"CODEX_API_KEY": "k"},
        codex_home="/tmp/codex_home",
        extra_env={"MY_FLAG": "1", "NO_PROXY": "localhost"},
    )
    assert env["MY_FLAG"] == "1"
    # extra_env is the final, authoritative layer.
    assert env["NO_PROXY"] == "localhost"


def test_does_not_mutate_os_environ(monkeypatch):
    import os

    _seed_environ(monkeypatch)
    before = dict(os.environ)
    build_codex_subprocess_env(cli_env={}, codex_home="/tmp/h")
    assert dict(os.environ) == before
