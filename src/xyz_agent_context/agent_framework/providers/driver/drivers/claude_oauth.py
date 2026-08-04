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
    VERIFY_DEAD,
    VERIFY_OK,
    VERIFY_UNKNOWN,
    DriverHealth,
    VerifyVerdict,
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
        checked and (b) for token mode points at ``verify_live`` as
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

    async def verify_live(self) -> tuple[VerifyVerdict, str]:
        """Real end-to-end check for BOTH auth modes: one one-shot CLI call.

        The 2026-07-23 incident's probe lied ("logged in ✓" over a dead
        credential) because it only checked existence — fixed then for
        ``oauth_token`` only, and the host-``oauth`` Test path kept an
        unconditional pass until the 2026-07-31 codex P0 exposed the same
        lie across every host-CLI credential. Now both modes make an
        actual ``claude`` CLI request. Expensive (spawns the CLI, bills one
        tiny subscription call), so it runs only on explicit user action or
        a readiness edge, never from ``probe()``.

        Host-oauth is NOT "the CLI's own store": ``to_cli_env`` points
        ``CLAUDE_CONFIG_DIR`` at the ISOLATED oauth config dir (the #72 /
        2026-07-09 leak fix), whose ``.credentials.json`` only exists after
        ``_stage_claude_oauth_credentials`` copies it from the host store
        (Keychain / ~/.claude). The agent path stages on every spawn
        (adapters/claude/sdk.py); this path must stage too, or a fresh
        ``claude login`` that has never run a turn verifies as dead —
        the PR #224 review's Critical.

        Tri-state: "dead" only when the CLI itself refused or there is
        verifiably nothing to try; environment gaps (control-plane node,
        timeout, no launchable CLI binary) are "unknown" and must not
        block readiness recovery. The control-plane guard covers ONLY
        host-oauth: token mode is node-independent (credential rides the
        env, the backend image ships the claude CLI) and stays verifiable
        on the control plane — as it has been since 2026-07-23.

        A single tool-free turn is NOT the agent_loop, so bounding it with
        helper-scale timeouts does not violate 铁律 #14 (same rationale as
        ``CliHelperSDK._run_claude_oneshot``). Never logs or returns the
        token itself.
        """
        import asyncio

        from xyz_agent_context.agent_framework.adapters.claude.cli_binary import (
            resolve_cli_path,
        )
        from xyz_agent_context.agent_framework.loop.broker_client import (
            executor_seam_active,
        )
        from xyz_agent_context.settings import settings as _settings

        if self._is_token_mode():
            # Token mode is node-independent — the credential rides the env
            # (CLAUDE_CODE_OAUTH_TOKEN) and the backend image ships the
            # claude CLI, so verifying ON the control plane is valid (and
            # has been since 2026-07-23). The seam guard below is only for
            # host-credential-store modes.
            if not (self.card.api_key or ""):
                return VERIFY_DEAD, "no setup-token stored"
        else:
            # Control-plane guard BEFORE any local inspection: host-oauth
            # judges the HOST credential store, and with the executor seam
            # active (BROKER_URL on dev/prod, AGENT_EXECUTOR_URL as the
            # static fallback) this container's store says nothing about
            # the machine that actually runs the CLI.
            if executor_seam_active():
                return VERIFY_UNKNOWN, (
                    "cannot verify from the control plane — the claude CLI "
                    "runs on the per-user executor"
                )
            # Don't spend a CLI spawn when the credential store is
            # verifiably empty — probe() already knows how to look
            # (credentials file, macOS Keychain).
            health = await self.probe()
            if not health.ok:
                return VERIFY_DEAD, health.detail

        env = self.build_claude_config("haiku").to_cli_env()
        env["API_TIMEOUT_MS"] = str(_settings.helper_cli_timeout_ms)
        env["CLAUDE_CODE_MAX_RETRIES"] = "0"

        if not self._is_token_mode():
            # Stage the host credential into the isolated CLAUDE_CONFIG_DIR —
            # the same call the agent adapter makes before every spawn.
            # Without it a healthy, freshly-logged-in host credential fails
            # verification on any install that has not run an agent turn yet.
            from xyz_agent_context.agent_framework.adapters.claude.sdk import (
                _stage_claude_oauth_credentials,
            )

            try:
                _stage_claude_oauth_credentials(env["CLAUDE_CONFIG_DIR"])
            except Exception as exc:  # noqa: BLE001 — staging is environmental
                summary = str(exc).splitlines()[0][:200]
                return VERIFY_UNKNOWN, f"could not stage credentials: {summary}"

        async def _one_shot() -> tuple[VerifyVerdict, str]:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                TextBlock,
                query,
            )

            # cli_path mirrors the agent adapter (adapters/claude/sdk.py):
            # resolve_cli_path() honours CLAUDE_CLI_PATH pins and fail-opens
            # to the SDK's bundled CLI on None — verification must run the
            # SAME binary the agent runs, and must not require a PATH
            # `claude` that the agent itself doesn't need.
            options_kwargs: dict = dict(
                env=env,
                model="haiku",
                max_turns=1,
                allowed_tools=[],
                system_prompt="Reply with exactly: OK",
            )
            cli_path = resolve_cli_path()
            if cli_path is not None:
                options_kwargs["cli_path"] = cli_path
            options = ClaudeAgentOptions(**options_kwargs)
            got_text = False
            async for message in query(prompt="ping", options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            got_text = True
            if got_text:
                return VERIFY_OK, "credentials verified — live CLI call succeeded"
            return VERIFY_DEAD, "CLI run produced no reply — credentials may be invalid"

        try:
            return await asyncio.wait_for(
                _one_shot(),
                timeout=_settings.helper_cli_total_timeout_seconds,
            )
        except asyncio.TimeoutError:
            # A hung CLI is indistinguishable from a slow network — not a
            # credential verdict.
            return VERIFY_UNKNOWN, (
                "verification timed out after "
                f"{_settings.helper_cli_total_timeout_seconds}s"
            )
        except Exception as exc:  # noqa: BLE001 — verdict, not control flow
            # Unlike codex (terminal error EVENTS), the claude SDK surfaces
            # auth failures as process errors — an exception here IS the
            # CLI refusing, so it maps to "dead". One carve-out: the SDK's
            # CLINotFoundError means no binary could be launched at all
            # (resolve_cli_path fail-opens to the bundled CLI, so this is a
            # broken install, not a credential verdict) — undecidable.
            # Summarize the first line only so env/token material can
            # never leak.
            summary = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            if "CLINotFound" in type(exc).__name__:
                return VERIFY_UNKNOWN, f"claude CLI unavailable: {summary}"
            return VERIFY_DEAD, f"live verification failed: {summary}"

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
