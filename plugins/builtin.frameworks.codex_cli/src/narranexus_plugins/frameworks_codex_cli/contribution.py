"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.frameworks.codex_cli — the agent-loop framework contribution (driver factory + install spec).
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta, InstallComponent
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.agent_framework import plugin_paths


def _factory(**factory_kwargs):
    plugin_paths.activate_pyenv()
    from narranexus_plugins.frameworks_codex_cli.official_sdk import CodexSDKv2

    return CodexSDKv2(**factory_kwargs)


INSTALL = FrameworkInstall(
    components=(InstallComponent(kind="pip", requirement="openai-codex==0.1.0b3"),),
    probe_package="openai_codex",
    user_version_source="pip_pkg",
    size_hint="~60 MB",
)
META = FrameworkMeta(
    "codex_cli",
    "Codex CLI",
    install=INSTALL,
    protocol="openai",
    oauth_source="codex_oauth",
    login_marker=(".codex", "auth.json"),
    # Authenticates from ``~/.codex/auth.json`` in the host's single HOME.
    uses_shared_cli_login=True,
    # ``CodexSDKv2.capabilities()`` is the base contract; CLI-flattened history.
    capabilities=frozenset(),
)
CONTRIBUTION = Contribution("codex_cli", lambda: _factory, meta={"framework": META})

__all__ = ["CONTRIBUTION", "INSTALL", "META"]
