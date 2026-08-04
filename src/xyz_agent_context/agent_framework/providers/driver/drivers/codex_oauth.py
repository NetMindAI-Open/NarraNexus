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

from xyz_agent_context.agent_framework.api_config import (
    CliHelperConfig,
    CodexConfig,
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
    CODEX_CLI_CREDENTIALS_REF,
    resolve_codex_credentials_path,
)
from xyz_agent_context.agent_framework.providers.driver.registry import register


@register
class CodexOAuthDriver(_DriverBase):
    """OpenAI Codex CLI OAuth provider — token lives in the host CLI."""

    @classmethod
    def driver_type(cls) -> str:
        return "codex_oauth"

    # build_claude_config / build_openai_config keep the _DriverBase
    # NotImplementedError defaults — Codex is not anthropic, and the OAuth
    # credential can't serve chat-completions DIRECTLY. The agent slot uses
    # build_codex_config; the helper slot uses build_cli_helper_config
    # (framework="codex_cli"), which runs one-shot through the same codex CLI
    # so a single subscription covers both slots.

    def build_cli_helper_config(self, model: str) -> CliHelperConfig:
        return CliHelperConfig(
            framework="codex_cli",
            model=model,
            base_url=self.card.base_url or "",
            auth_type=self.card.auth_type or "oauth",
            api_key=self.card.api_key or "",
        )

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

    async def verify_live(self) -> tuple[VerifyVerdict, str]:
        """Real end-to-end check: one one-shot ``codex exec`` call.

        The P0 this fixes (2026-07-31): the Test button answered every
        ``auth_type == "oauth"`` row with an unconditional pass, so expired
        codex CLI credentials showed "usable" — and ProviderReadiness
        re-armed paused jobs onto them. Existence of ``auth.json`` is not
        health: the file survives while its refresh token dies. The only
        honest verdict is the same transport the agent uses, so this runs
        one tool-free turn through the registered codex agent-loop driver
        (via the shared ``run_codex_cli_oneshot``).

        Tri-state, not bool: this process may not be the node that runs the
        CLI at all. On cloud the agent loop executes in broker-managed
        per-user executor containers (``BROKER_URL`` — what the deploy
        stack actually sets; ``AGENT_EXECUTOR_URL`` is the static
        fallback, see ``executor_seam_active``) and the control-plane
        image does not ship ``codex`` — every local check here would
        misread "different node" as "dead credential" and permanently
        block the readiness edge that re-arms paused jobs. That case is
        "unknown", never "dead".

        The codex driver reads its config from the ambient ``_codex_ctx``
        ContextVar (the AGENT slot's config, not this card's), so the
        card's own CodexConfig is installed for the duration and reset
        after. Dead credentials surface as a terminal error EVENT
        (error_type="unauthorized"), not an exception.

        Expensive (spawns the CLI), so it runs only on explicit user
        action or a readiness edge, never from ``probe()``. A single
        tool-free turn is NOT the agent_loop — helper-scale timeouts do
        not violate 铁律 #14.
        """
        import asyncio
        import os
        import shutil

        from xyz_agent_context.agent_framework.loop.broker_client import (
            executor_seam_active,
        )

        # Control-plane guard BEFORE any local inspection: with the executor
        # seam active (broker-managed per-user executors via BROKER_URL — the
        # shape dev/prod compose actually deploys — or the static
        # AGENT_EXECUTOR_URL fallback), this container's PATH and ~/.codex say
        # nothing about the machine that actually runs the CLI.
        if executor_seam_active():
            return VERIFY_UNKNOWN, (
                "cannot verify from the control plane — the codex CLI runs "
                "on the per-user executor"
            )

        # Fail fast without spawning: no credentials file → nothing to try.
        health = await self.probe()
        if not health.ok:
            return VERIFY_DEAD, health.detail
        if shutil.which("codex") is None:
            return VERIFY_DEAD, (
                "codex CLI not found on PATH — install it (or run "
                "`codex login` on the machine that has it)"
            )

        from xyz_agent_context.agent_framework.api_config import _codex_ctx
        from xyz_agent_context.agent_framework.llm.cli_oneshot import (
            oneshot_cwd,
            run_codex_cli_oneshot,
        )
        from xyz_agent_context.agent_framework.providers.model_catalog import (
            get_default_models,
        )
        from xyz_agent_context.settings import settings as _settings

        # Curated default first: stored `models` may carry pinned ids that
        # upstream has retired (the 2026-07-30 dead-pinned-id lesson), and a
        # verification must not fail on a dead model name while the
        # credential is fine. The curated list lives in the catalog
        # (single source with user_service.CODEX_CURATED_MODELS).
        defaults = get_default_models("codex_oauth", "openai")
        model = defaults[0] if defaults else (self.card.models[0] if self.card.models else "")
        if not model:
            # A missing model list is a config gap, not a credential verdict.
            return VERIFY_UNKNOWN, "no codex model available to verify with"

        token = _codex_ctx.set(self.build_codex_config(model))
        try:
            result = await asyncio.wait_for(
                run_codex_cli_oneshot(
                    "Reply with exactly: OK",
                    "ping",
                    working_path=oneshot_cwd("verify"),
                ),
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
            # Codex reports credential failures as terminal error EVENTS
            # (handled below); an exception here is environmental (spawn
            # failure, seam error) — undecidable, not dead.
            summary = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            return VERIFY_UNKNOWN, f"could not run live verification: {summary}"
        finally:
            _codex_ctx.reset(token)

        if result.error:
            return VERIFY_DEAD, f"live verification failed: {result.error[:200]}"
        if result.text.strip():
            return VERIFY_OK, "credentials verified — live codex CLI call succeeded"
        return VERIFY_DEAD, "CLI run produced no reply — credentials may be invalid"

    async def probe(self) -> DriverHealth:
        """Check whether the host CLI credentials file actually exists.

        Existence is the CHEAP signal for the Settings page ("✓ Codex CLI
        linked" vs "✗ run `codex login`") — it is NOT health; the honest
        verdict is ``verify_live``. Like ClaudeOAuthDriver.
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
