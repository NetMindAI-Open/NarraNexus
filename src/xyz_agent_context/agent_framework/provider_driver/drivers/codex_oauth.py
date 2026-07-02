"""
@file_name: codex_oauth.py
@date: 2026-05-29
@description: Driver for OpenAI Codex CLI OAuth (host-CLI managed) provider.

The Codex CLI on the host machine performs the OAuth flow (``codex
login`` → Sign in with ChatGPT) and stores the resulting tokens in
``~/.codex/auth.json`` (or ``$CODEX_HOME/auth.json``). NarraNexus
does NOT store the tokens itself; the ``user_providers`` row
carries:

* ``api_key`` = empty string (intentionally — see CodexConfig.to_cli_env)
* ``auth_type`` = ``"oauth"``
* ``auth_ref`` = ``"codex-cli:~/.codex/auth.json"``
* ``supports_anthropic_server_tools`` = False (Codex is OpenAI; no
  Anthropic server tools)

The ``codex exec`` subprocess reads the token from the credentials
file on its own when ``CODEX_API_KEY`` is empty. The
``CodexConfig.to_cli_env`` builder already produces that empty-key
shape for ``auth_type="oauth"``.

OAuth rows can't serve the helper_llm or embedding slot — those need
chat-completions / embedding endpoints, neither of which Codex
provides via the OAuth credential. The agent slot is served through
:meth:`build_codex_config` (the codex_cli framework path): this driver
overrides it to force the shared CLI credential ref
(``CODEX_CLI_CREDENTIALS_REF``) so the ``codex exec`` subprocess reads
the token from ``~/.codex/auth.json`` rather than an env var.
``build_claude_config`` / ``build_openai_config`` stay
NotImplementedError — Codex is not an anthropic provider and the OAuth
credential can't serve chat-completions.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import CodexConfig
from xyz_agent_context.agent_framework.provider_driver.base import (
    DriverHealth,
    _DriverBase,
)
from xyz_agent_context.agent_framework.provider_driver.derive import (
    CODEX_CLI_CREDENTIALS_REF,
    resolve_codex_credentials_path,
)
from xyz_agent_context.agent_framework.provider_driver.registry import register


@register
class CodexOAuthDriver(_DriverBase):
    """OpenAI Codex CLI OAuth provider — token lives in the host CLI."""

    @classmethod
    def driver_type(cls) -> str:
        return "codex_oauth"

    # build_claude_config / build_openai_config keep the _DriverBase
    # NotImplementedError defaults — Codex is not anthropic, and the OAuth
    # credential can't serve chat-completions. Only build_codex_config
    # (agent slot, codex_cli framework) is overridden below.

    def build_codex_config(
        self,
        model: str,
        *,
        thinking: str = "",
        reasoning_effort: str = "",
    ) -> CodexConfig:
        # OAuth: the token lives in the host CLI's auth.json, not in the
        # card. Force the canonical credential ref so the run-time stager
        # copies ~/.codex/auth.json into the per-run CODEX_HOME; leave
        # api_key empty (to_cli_env blanks CODEX_API_KEY for oauth).
        auth_type = (self.card.auth_type or "oauth")
        auth_ref = (
            CODEX_CLI_CREDENTIALS_REF
            if auth_type.lower() == "oauth"
            else (self.card.auth_ref or "")
        )
        return CodexConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=auth_type,
            auth_ref=auth_ref,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )

    async def probe(self) -> DriverHealth:
        """Check whether the host CLI credentials file actually exists.

        Like ClaudeOAuthDriver — existence is a sufficient signal for
        the Settings page to show "✓ Codex CLI linked" vs "✗ run
        `codex login`".
        """
        path = resolve_codex_credentials_path(self.card.auth_ref)
        if path is None:
            return DriverHealth(
                ok=False,
                detail="auth_ref is missing or not a codex-cli: reference",
            )
        if not path.exists():
            return DriverHealth(
                ok=False,
                detail=(
                    f"credentials file not found at {path}. "
                    f"Run `codex login` on the host to create it."
                ),
            )
        if not path.is_file():
            return DriverHealth(
                ok=False,
                detail=f"credentials path exists but is not a file: {path}",
            )
        return DriverHealth(ok=True, detail=f"credentials present at {path}")
