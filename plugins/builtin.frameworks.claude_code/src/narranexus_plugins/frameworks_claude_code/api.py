"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of builtin.frameworks.claude_code — what another plugin may import: the CLI pin, the CLI resolver, the OAuth credential staging and the driver class.
"""
from __future__ import annotations

from narranexus_plugins.frameworks_claude_code.cli_binary import PINNED_CLI_VERSION, resolve_cli_path

PLUGIN_ID = "builtin.frameworks.claude_code"
PACKAGE = "narranexus_plugins.frameworks_claude_code"


def stage_oauth_credentials(config_dir: str) -> None:
    """Stage the host's Claude OAuth credentials into ``config_dir`` (macOS Keychain export included)."""
    from narranexus_plugins.frameworks_claude_code.sdk import _stage_claude_oauth_credentials

    _stage_claude_oauth_credentials(config_dir)


def driver_class():
    from narranexus_plugins.frameworks_claude_code.sdk import ClaudeAgentSDK

    return ClaudeAgentSDK


__all__ = ["PACKAGE", "PINNED_CLI_VERSION", "PLUGIN_ID", "driver_class", "resolve_cli_path", "stage_oauth_credentials"]
