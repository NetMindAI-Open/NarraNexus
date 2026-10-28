"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.frameworks.claude_code — the agent-loop framework contribution (driver factory + install spec).
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta, InstallComponent
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.agent_framework import plugin_paths
from narranexus_plugins.frameworks_claude_code.cli_binary import PINNED_CLI_VERSION


def _factory(**factory_kwargs):
    plugin_paths.activate_pyenv()
    from narranexus_plugins.frameworks_claude_code.sdk import ClaudeAgentSDK

    return ClaudeAgentSDK(**factory_kwargs)


INSTALL = FrameworkInstall(
    components=(
        InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43"),
        InstallComponent(kind="npm", requirement=f"@anthropic-ai/claude-code@{PINNED_CLI_VERSION}"),
    ),
    probe_package="claude_agent_sdk",
    user_version_source="npm_cli",
    size_hint="~190 MB",
)
META = FrameworkMeta(
    "claude_code",
    "Claude Code",
    install=INSTALL,
    protocol="anthropic",
    oauth_source="claude_oauth",
    # The picker says "Claude Code"; the agent introduces its runtime by the
    # SDK it actually is (the prompt derives from this string).
    runtime_name="Claude Agent SDK",
    login_marker=(".claude", ".credentials.json"),
    # The CLI authenticates from ``~/.claude/.credentials.json`` — a file in
    # the host's single HOME — so it CAN ride a shared login. Whether cloud
    # nonetheless offers it is the operator's call, not this plugin's (see
    # ``providers/cloud_policy.py``): a plugin attesting its own cloud safety
    # would be fail-open by construction.
    uses_shared_cli_login=True,
    # ``ClaudeAgentSDK.capabilities()`` is the base contract; its history is
    # flattened at the CLI doorstep, so no ``native_replay`` either.
    capabilities=frozenset(),
)
CONTRIBUTION = Contribution("claude_code", lambda: _factory, meta={"framework": META})

__all__ = ["CONTRIBUTION", "INSTALL", "META"]
