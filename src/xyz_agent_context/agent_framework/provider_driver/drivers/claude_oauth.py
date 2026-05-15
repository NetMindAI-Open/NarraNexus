"""
@file_name: claude_oauth.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for Claude Code OAuth (host-CLI managed) provider.

The Claude Code CLI on the host machine performs the OAuth flow with
Anthropic and stores the resulting tokens in
``~/.claude/.credentials.json`` (or wherever ``CLAUDE_CLI_HOME`` /
``CLAUDE_CLI_CREDENTIALS_PATH`` say). NarraNexus does NOT store the
tokens itself; the ``user_providers`` row carries:

* ``api_key`` = empty string (intentionally — see api_config.to_cli_env)
* ``auth_type`` = ``"oauth"``
* ``auth_ref`` = ``"claude-cli:~/.claude/.credentials.json"``
* ``supports_anthropic_server_tools`` = True (this is the official
  Anthropic backend after all)

The Claude Code CLI subprocess reads the token from the credentials
file on its own when ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN``
are both empty. The ``ClaudeConfig.to_cli_env`` builder already does
the right thing for that case, so this Driver just produces a
ClaudeConfig with empty api_key + auth_type="oauth".

OAuth rows can't serve the helper_llm or embedding slot — those need
chat-completions / embedding endpoints, neither of which Claude
provides via the OAuth credential.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.provider_driver.base import (
    DriverHealth,
    _DriverBase,
)
from xyz_agent_context.agent_framework.provider_driver.derive import (
    resolve_claude_credentials_path,
)
from xyz_agent_context.agent_framework.provider_driver.registry import register


@register
class ClaudeOAuthDriver(_DriverBase):
    """Claude Code OAuth provider — token lives in the host CLI."""

    @classmethod
    def driver_type(cls) -> str:
        return "claude_oauth"

    def build_claude_config(self, model: str) -> ClaudeConfig:
        return ClaudeConfig(
            api_key="",  # intentional: tells to_cli_env to blank both env vars
            base_url=self.card.base_url or "",
            model=model,
            auth_type="oauth",
            supports_anthropic_server_tools=True,
        )

    async def probe(self) -> DriverHealth:
        """Check whether the host CLI credentials file actually exists.

        We don't parse the token — that's the CLI's job. Existence is a
        sufficient signal for the Settings page to show "✓ Claude OAuth
        linked" vs "✗ run `claude auth login`".
        """
        path = resolve_claude_credentials_path(self.card.auth_ref)
        if path is None:
            return DriverHealth(
                ok=False,
                detail="auth_ref is missing or not a claude-cli: reference",
            )
        if not path.exists():
            return DriverHealth(
                ok=False,
                detail=f"credentials file not found at {path}",
            )
        if not path.is_file():
            return DriverHealth(
                ok=False,
                detail=f"credentials path exists but is not a file: {path}",
            )
        return DriverHealth(ok=True, detail=f"credentials present at {path}")
