"""
@file_name: system.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for the cloud-only system free-tier pool.

The system free-tier is the credential the operator provides to give
every cloud user a starter budget. It's gated by:

1. ``is_cloud_mode()`` — local DMG / `bash run.sh` never registers
   this driver and never reads its row.
2. ``user_quotas.prefer_system_override`` — the user has opted in to
   the free tier. (During the migration window, the resolver still
   checks this flag in addition to the slot binding so the legacy
   semantic survives.)

When a SystemDriver-backed slot completes an LLM call,
:meth:`on_call_completed` debits the user's ``user_quotas`` row via
the existing ``quota_service.deduct`` API. That's the **only**
billing-side difference between this driver and the user-pays
drivers — everything else flows through the same cost_records audit
log.

The card row for the system pool is created by the cloud migration
script (see spec §4.6) with ``owner_user_id=NULL`` so it's visible
to every user.
"""
from __future__ import annotations

from loguru import logger

from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    OpenAIConfig,
)
from xyz_agent_context.agent_framework.provider_driver.base import (
    CallContext,
    _DriverBase,
)
from xyz_agent_context.agent_framework.provider_driver.registry import register
from xyz_agent_context.utils.deployment_mode import is_cloud_mode


class SystemDriver(_DriverBase):
    """Cloud-only system free-tier pool driver."""

    @classmethod
    def driver_type(cls) -> str:
        return "system_pool"

    def _is_anthropic_row(self) -> bool:
        return (self.card.protocol or "").lower() == "anthropic"

    def _is_openai_row(self) -> bool:
        return (self.card.protocol or "").lower() == "openai"

    def build_claude_config(self, model: str) -> ClaudeConfig:
        if not self._is_anthropic_row():
            raise NotImplementedError(
                f"SystemDriver on protocol={self.card.protocol!r} can't serve agent slot"
            )
        return ClaudeConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=self.card.auth_type or "bearer_token",
            supports_anthropic_server_tools=bool(
                self.card.supports_anthropic_server_tools
            ),
        )

    def build_openai_config(self, model: str) -> OpenAIConfig:
        if not self._is_openai_row():
            raise NotImplementedError(
                f"SystemDriver on protocol={self.card.protocol!r} can't serve helper_llm slot"
            )
        return OpenAIConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
        )

    def build_anthropic_helper_config(self, model: str) -> AnthropicHelperConfig:
        if not self._is_anthropic_row():
            raise NotImplementedError(
                f"SystemDriver on protocol={self.card.protocol!r} can't serve "
                f"helper_llm (anthropic) slot"
            )
        return AnthropicHelperConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=self.card.auth_type or "bearer_token",
        )

    async def on_call_completed(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        ctx: CallContext,
    ) -> None:
        """Deduct the call's token usage from the user's quota.

        Failure to deduct is logged but never raised — the LLM call
        already succeeded and we shouldn't fail the user-facing path
        because of an accounting hiccup.
        """
        if not ctx.user_id:
            logger.warning(
                "[SystemDriver] on_call_completed with empty user_id — skipping deduct"
            )
            return

        try:
            from xyz_agent_context.agent_framework.quota_service import QuotaService

            qs = QuotaService.default()
            await qs.deduct(ctx.user_id, input_tokens, output_tokens)
        except Exception as e:  # noqa: BLE001 — defensive, never block on this
            logger.warning(
                f"[SystemDriver] quota deduct failed for user_id={ctx.user_id!r}: {e}"
            )


# Cloud-only registration: local mode never has the env-backed system
# credential, and we don't want a stray SystemDriver lying in the
# registry waiting to be selected by a misconfigured row.
if is_cloud_mode():
    SystemDriver = register(SystemDriver)  # type: ignore[assignment]
