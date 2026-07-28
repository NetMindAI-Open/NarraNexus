"""
@file_name: claude_oauth.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for Claude subscription providers — host-CLI OAuth and
setup-token.

One card (source ``claude_oauth``), two credential transports selected by
``auth_type``:

* ``oauth`` — the host Claude Code CLI owns the credential (Keychain on
  macOS, ``~/.claude/.credentials.json`` on Linux). The ``user_providers``
  row carries ``api_key=""`` and the ``claude-cli:`` sentinel in
  ``auth_ref``; the credential file is staged into the isolated OAuth
  config dir before each spawn (``_stage_claude_oauth_credentials``).
* ``oauth_token`` — a long-lived token minted by ``claude setup-token``,
  stored in ``api_key`` and injected as the ``CLAUDE_CODE_OAUTH_TOKEN``
  env var. No staging, no Keychain, no auth_ref. This is the officially
  documented headless channel and the cure for the 2026-07-23 macOS
  incident: the CLI imports staged credentials into a
  config-dir-namespaced Keychain entry ONCE and never reads the staged
  file again, so the frozen copy dies as the host's OAuth family rotates
  — while env-injected tokens bypass the CLI credential store entirely.

Both transports set ``supports_anthropic_server_tools=True`` (official
Anthropic backend) and serve the helper_llm slot through the same CLI
(``build_cli_helper_config``, framework="claude_code") — one subscription
covers both slots.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CliHelperConfig,
)
from xyz_agent_context.agent_framework.providers.driver.base import (
    DriverHealth,
    _DriverBase,
)
from xyz_agent_context.agent_framework.providers.driver.derive import (
    resolve_claude_credentials_path,
)
from xyz_agent_context.agent_framework.providers.driver.registry import register

# `claude setup-token` mints tokens with this prefix (observed, not a
# documented contract — hence a soft signal in probe details, never a
# validation gate).
_SETUP_TOKEN_PREFIX = "sk-ant-oat"


@register
class ClaudeOAuthDriver(_DriverBase):
    """Claude subscription provider — host-CLI OAuth or setup-token."""

    @classmethod
    def driver_type(cls) -> str:
        return "claude_oauth"

    def _is_token_mode(self) -> bool:
        return (self.card.auth_type or "").lower() == "oauth_token"

    def build_claude_config(self, model: str) -> ClaudeConfig:
        return ClaudeConfig(
            # oauth: empty key tells to_cli_env to blank both ANTHROPIC_*
            # vars so the CLI reads its own credential store.
            # oauth_token: the token rides api_key and to_cli_env injects it
            # as CLAUDE_CODE_OAUTH_TOKEN.
            api_key=self.card.api_key or "",
            base_url=self.card.base_url or "",
            model=model,
            auth_type=self.card.auth_type or "oauth",
            supports_anthropic_server_tools=True,
        )

    def build_cli_helper_config(self, model: str) -> CliHelperConfig:
        # Same subscription, run one-shot through the claude CLI for the
        # helper slot — no separate API key.
        return CliHelperConfig(
            framework="claude_code",
            model=model,
            base_url=self.card.base_url or "",
            auth_type=self.card.auth_type or "oauth",
            api_key=self.card.api_key or "",
        )

    async def probe(self) -> DriverHealth:
        """Cheap credential-presence check for the Settings page.

        2026-07-23 lesson: presence is NOT health — a Keychain entry can
        exist while its token family is dead, and this probe used to check
        a DIFFERENT store (the unsuffixed host entry) than the one the
        runtime read. So the probe now (a) says exactly which store it
        checked and (b) for token mode points at ``verify_token_live`` as
        the real verdict. It stays cheap (no network) because the Settings
        page calls it on every load.
        """
        if self._is_token_mode():
            token = self.card.api_key or ""
            if not token:
                return DriverHealth(
                    ok=False,
                    detail=(
                        "auth_type is oauth_token but no token is stored — "
                        "run `claude setup-token` and paste the token in "
                        "Settings → LLM Providers"
                    ),
                )
            note = ""
            if not token.startswith(_SETUP_TOKEN_PREFIX):
                note = (
                    " (warning: token does not look like `claude setup-token` "
                    f"output — expected a {_SETUP_TOKEN_PREFIX}… prefix)"
                )
            return DriverHealth(
                ok=True,
                detail=(
                    "setup-token stored; use Test connection for a live "
                    "verification" + note
                ),
            )

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

    async def verify_token_live(self) -> tuple[bool, str]:
        """Real end-to-end check for token mode: one one-shot CLI call.

        The 2026-07-23 incident's probe lied ("logged in ✓" over a dead
        credential) because it only checked existence. For ``oauth_token``
        the credential is in OUR hands, so the explicit Test button makes
        an actual ``claude`` CLI request with the stored token — the same
        transport the agent loop uses. Expensive (spawns the CLI, bills one
        tiny subscription call), so it runs only on explicit user action,
        never from ``probe()``.

        A single tool-free turn is NOT the agent_loop, so bounding it with
        helper-scale timeouts does not violate 铁律 #14 (same rationale as
        ``CliHelperSDK._run_claude_oneshot``). Never logs or returns the
        token itself.
        """
        import asyncio
        import shutil

        from xyz_agent_context.settings import settings as _settings

        if not (self.card.api_key or ""):
            return False, "no setup-token stored"
        if shutil.which("claude") is None:
            return False, "claude CLI not found on PATH — cannot verify"

        env = self.build_claude_config("haiku").to_cli_env()
        env["API_TIMEOUT_MS"] = str(_settings.helper_cli_timeout_ms)
        env["CLAUDE_CODE_MAX_RETRIES"] = "0"

        async def _one_shot() -> tuple[bool, str]:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                TextBlock,
                query,
            )

            options = ClaudeAgentOptions(
                env=env,
                model="haiku",
                max_turns=1,
                allowed_tools=[],
                system_prompt="Reply with exactly: OK",
            )
            got_text = False
            async for message in query(prompt="ping", options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            got_text = True
            if got_text:
                return True, "setup-token verified — live CLI call succeeded"
            return False, "CLI run produced no reply — token may be invalid"

        try:
            return await asyncio.wait_for(
                _one_shot(),
                timeout=_settings.helper_cli_total_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return False, (
                "verification timed out after "
                f"{_settings.helper_cli_total_timeout_seconds}s"
            )
        except Exception as exc:  # noqa: BLE001 — verdict, not control flow
            # The SDK surfaces auth failures as process errors; summarize
            # the first line only so env/token material can never leak.
            summary = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            return False, f"live verification failed: {summary}"

    @staticmethod
    async def _keychain_has_credentials() -> bool:
        """True when the macOS Keychain holds Claude Code's OAuth token.

        Uses ``security find-generic-password`` (exit 0 = found). Never
        reads or logs the secret itself; existence only. Non-macOS or any
        error → False (fall through to the file-based verdict).

        Caveat (2026-07-23): this checks the UNSUFFIXED host entry — the
        one the user's interactive CLI writes. Under an isolated
        ``CLAUDE_CONFIG_DIR`` the runtime CLI reads a config-dir-namespaced
        entry instead, so "present here" does not guarantee the runtime can
        authenticate. Kept as a presence signal only; token mode
        (``oauth_token``) avoids the Keychain entirely.
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
