"""
@file_name: system.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for the cloud-only system free-tier pool.

The system free-tier is the credential the operator provides to give
every cloud user a starter budget. It's gated by:

1. ``is_cloud_mode()`` — local DMG / `bash run.sh` never registers
   this driver and never reads its row.
2. a ``user_quotas`` row exists — the row IS the free-tier grant.
   (Free-tier-first is platform behavior since 2026-07-18; the old
   ``prefer_system_override`` opt-in column survives only as the
   exhaustion-notice latch, see provider_resolver.)

This driver builds credentials only — it does NOT bill. The free-tier
debit happens in ``utils.cost_tracker.record_cost``, which deducts from
``user_quotas`` whenever the ``provider_source`` context tag reads
``"system"``, in the same place it writes the ``cost_records`` row.

An earlier design had each driver debit its own quota from a post-call
hook; that hook was never wired to a dispatcher and cost_tracker became
the real implementation. The dead hook was removed 2026-07-20 — it had
been claiming, in this very docstring, to be the billing path. Do not
reintroduce per-driver billing without first removing cost_tracker's
deduct: two live paths would double-charge users.

The card row for the system pool is created by the cloud migration
script (see spec §4.6) with ``owner_user_id=NULL`` so it's visible
to every user.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    OpenAIConfig,
)
from xyz_agent_context.agent_framework.provider_driver.base import _DriverBase
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


# Cloud-only registration: local mode never has the env-backed system
# credential, and we don't want a stray SystemDriver lying in the
# registry waiting to be selected by a misconfigured row.
if is_cloud_mode():
    SystemDriver = register(SystemDriver)  # type: ignore[assignment]
