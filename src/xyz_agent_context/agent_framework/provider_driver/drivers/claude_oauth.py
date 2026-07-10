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

The helper_llm slot is served the SAME way — a subscription login covers
both the agent slot (``build_claude_config``) and the helper slot
(``build_cli_helper_config``, framework="claude_code"): the helper's small
structured-output calls run one-shot through the same ``claude`` CLI, so no
separate API key is needed (bug: "Claude Code subscription should also apply
to Helper LLM", 2026-07).
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CliHelperConfig,
)
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

    def build_cli_helper_config(self, model: str) -> CliHelperConfig:
        # Same subscription, run one-shot through the claude CLI for the
        # helper slot — no separate API key.
        return CliHelperConfig(
            framework="claude_code",
            model=model,
            base_url=self.card.base_url or "",
            auth_type="oauth",
            api_key="",
        )

    async def probe(self) -> DriverHealth:
        """Check whether the host CLI credentials actually exist.

        We don't parse the token — that's the CLI's job. Existence is a
        sufficient signal for the Settings page to show "✓ Claude OAuth
        linked" vs "✗ run `claude auth login`".

        Two storage backends, checked in order:
        1. The credentials FILE (~/.claude/.credentials.json or the
           CLAUDE_CLI_* overrides) — Linux/containers.
        2. The macOS KEYCHAIN — Claude Code on macOS stores the OAuth token
           as a "Claude Code-credentials" generic password and never writes
           the file, so the file-only probe false-negatived on every Mac
           ("credentials file not found" while the CLI worked fine).
        """
        path = resolve_claude_credentials_path(self.card.auth_ref)
        if path is None:
            return DriverHealth(
                ok=False,
                detail="auth_ref is missing or not a claude-cli: reference",
            )
        if path.is_file():
            return DriverHealth(ok=True, detail=f"credentials present at {path}")
        if await self._keychain_has_credentials():
            return DriverHealth(
                ok=True, detail="credentials present in macOS Keychain"
            )
        if path.exists():
            return DriverHealth(
                ok=False,
                detail=f"credentials path exists but is not a file: {path}",
            )
        return DriverHealth(
            ok=False,
            detail=f"credentials file not found at {path}",
        )

    @staticmethod
    async def _keychain_has_credentials() -> bool:
        """True when the macOS Keychain holds Claude Code's OAuth token.

        Uses ``security find-generic-password`` (exit 0 = found). Never
        reads or logs the secret itself; existence only. Non-macOS or any
        error → False (fall through to the file-based verdict).
        """
        import asyncio
        import sys

        if sys.platform != "darwin":
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "security", "find-generic-password",
                "-s", "Claude Code-credentials",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return (await proc.wait()) == 0
        except Exception:  # noqa: BLE001 — probe is best-effort
            return False
